"""
Covered-call advisor: daily DTE + strike/price recommendations for held shares.

Positions come from the CC_POSITIONS env var, e.g. "META:210,GOOGL:1000".
When unset the section is skipped entirely, so the public harness never
carries personal holdings.

Strategy rules (calibrated 2026-08-03):
- Open at 30-55 DTE, targeting ~45; prefer monthly (3rd-Friday) expiries
  for open interest and tight spreads.
- Never let a short call cross earnings: the expiry must end >= 2 days
  before the next confirmed earnings date. If every 25+ DTE expiry crosses
  earnings, advise waiting for the post-earnings IV crush instead.
- Offer a regime-adaptive strike ladder with mid-price limit quotes:
  trending 0.15/0.20/0.25 delta, neutral 0.20/0.25/0.30, basing
  0.25/0.30/0.35.
- Coverage ratio scales with momentum regime: stocks near 52-week highs
  get LESS coverage (empirical breach rates run far above risk-neutral
  P(ITM) on trending names), basing stocks get MORE.
- Warn when IV runs below trimmed realized vol (thin premium).
- Exit/roll guidance: GTC buyback at 50% of fill, else forced roll at
  21 DTE.
"""
import os
from datetime import datetime, timedelta
from math import erf, log, sqrt

import numpy as np
import pandas as pd
import yfinance as yf

TARGET_DTE = 45
DTE_MIN, DTE_MAX = 25, 60
EARNINGS_BUFFER_DAYS = 2
RISK_FREE = 0.037  # refreshed from ^IRX when available
VRP_THIN_PT = -1.0  # IV below realized vol by >1pt = thin premium, warn


def _norm_cdf(x):
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def _bs_call_delta(spot, strike, t_years, rate, sigma):
    if t_years <= 0 or sigma <= 0:
        return 1.0 if spot > strike else 0.0
    d1 = (log(spot / strike) + (rate + sigma**2 / 2) * t_years) / (sigma * sqrt(t_years))
    return _norm_cdf(d1)


def get_positions():
    """Parse CC_POSITIONS ("META:210,GOOGL:1000") into {ticker: shares}."""
    raw = os.environ.get("CC_POSITIONS", "").strip()
    positions = {}
    for part in raw.split(","):
        if ":" not in part:
            continue
        ticker, _, shares = part.partition(":")
        try:
            positions[ticker.strip().upper()] = int(shares)
        except ValueError:
            continue
    return positions


def _risk_free_rate():
    try:
        irx = yf.Ticker("^IRX").history(period="5d")["Close"]
        if len(irx):
            return float(irx.iloc[-1]) / 100
    except Exception:
        pass
    return RISK_FREE


def _next_earnings(t):
    """Next earnings date as a date, or None if unavailable."""
    try:
        dates = t.calendar.get("Earnings Date") or []
        today = datetime.now().date()
        future = [d for d in dates if d >= today]
        return min(future) if future else None
    except Exception:
        return None


def _is_monthly(exp_date):
    """3rd Friday of the month (standard monthly expiry)."""
    return exp_date.weekday() == 4 and 15 <= exp_date.day <= 21


def _pick_expiry(expirations, earnings, today=None):
    """
    Choose the expiry: closest to TARGET_DTE within [DTE_MIN, DTE_MAX] and
    ending before earnings; monthlies win ties. Falls back to a shorter
    pre-earnings expiry (>=15 DTE), else signals WAIT.

    Returns (expiry_str, dte, status) where status is "ok", "short" or "wait".
    """
    today = today or datetime.now().date()
    cutoff = earnings - timedelta(days=EARNINGS_BUFFER_DAYS) if earnings else None

    candidates = []
    for exp in expirations:
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        dte = (exp_date - today).days
        if cutoff and exp_date > cutoff:
            continue
        candidates.append((exp, exp_date, dte))

    in_window = [c for c in candidates if DTE_MIN <= c[2] <= DTE_MAX]
    if in_window:
        # Prefer monthlies; among those, closest to target DTE
        monthlies = [c for c in in_window if _is_monthly(c[1])]
        pool = monthlies or in_window
        best = min(pool, key=lambda c: abs(c[2] - TARGET_DTE))
        return best[0], best[2], "ok"

    # No 25-60d window before earnings: take the longest pre-earnings expiry
    # with >=15 days left. Upper bound keeps a data gap in the 25-60 band
    # from silently selecting a far-dated expiry.
    short = [c for c in candidates if 15 <= c[2] <= DTE_MAX]
    if short:
        best = max(short, key=lambda c: c[2])
        return best[0], best[2], "short"

    return None, None, "wait"


def _momentum_regime(closes):
    """
    Classify drift risk -> (label, coverage lo/hi, delta ladder).

    The delta ladder shifts with regime, not just the coverage ratio:
    trending names breach far above risk-neutral P(ITM) (~1.5-1.7x
    empirically), so sell further OTM; basing names rarely breach, so
    harvest closer to the money.
    """
    spot = closes.iloc[-1]
    dist_high = spot / closes.tail(252).max() - 1
    ret_3m = spot / closes.iloc[-64] - 1 if len(closes) > 64 else 0.0
    # Within 8% of the 52w high counts as trending on its own: empirical
    # breach rates near highs run far above risk-neutral P(ITM) even when a
    # recent drawdown has dragged the 3m return negative (V-recovery case).
    if dist_high > -0.08 or (dist_high > -0.12 and ret_3m > 0.05):
        return "趋势强（接近52周高点）", 0.4, 0.6, (0.25, 0.20, 0.15)
    if dist_high < -0.18:
        return "回撤磨底（距高点>18%）", 0.8, 1.0, (0.35, 0.30, 0.25)
    return "中性震荡", 0.6, 0.8, (0.30, 0.25, 0.20)


def analyze_ticker(ticker, shares, rate):
    """Full covered-call recommendation for one position. Raises on data errors."""
    t = yf.Ticker(ticker)
    hist = t.history(period="1y")
    closes = hist["Close"]
    spot = float(closes.iloc[-1])

    rets = closes.pct_change().dropna()
    rv21 = float(rets.tail(21).std() * np.sqrt(252))
    last21 = rets.tail(21)
    trimmed = float(last21[last21.abs().rank(ascending=False) > 3].std() * np.sqrt(252))

    earnings = _next_earnings(t)
    expiry, dte, status = _pick_expiry(t.options, earnings)

    regime, cov_lo, cov_hi, ladder_deltas = _momentum_regime(closes)
    contracts = shares // 100

    result = {
        "ticker": ticker,
        "shares": shares,
        "contracts": contracts,
        "spot": spot,
        "rv21": rv21,
        "rv_trimmed": trimmed,
        "earnings": earnings,
        "expiry": expiry,
        "dte": dte,
        "status": status,
        "regime": regime,
        "cover_lo": max(1, round(contracts * cov_lo)) if contracts else 0,
        "cover_hi": max(1, round(contracts * cov_hi)) if contracts else 0,
        "ladder": [],
        "atm_iv": None,
        "exp_move": None,
    }
    if status == "wait" or expiry is None:
        return result

    chain = t.option_chain(expiry)
    calls = chain.calls
    # Quote sanity + liquidity floor: keeps a stale/crossed quote or a dead
    # strike from landing in the recommendation ladder.
    calls = calls[
        (calls.bid > 0) & (calls.ask >= calls.bid) & (calls.strike > spot)
        & ((calls.openInterest.fillna(0) >= 10) | (calls.volume.fillna(0) > 0))
    ].copy()
    if calls.empty:
        result["status"] = "wait"
        return result

    atm_idx = (chain.calls.strike - spot).abs().idxmin()
    result["atm_iv"] = float(chain.calls.loc[atm_idx, "impliedVolatility"])

    t_years = dte / 365
    result["exp_move"] = spot * result["atm_iv"] * sqrt(t_years)
    # yfinance per-strike IVs are occasionally stale garbage; clamp to a
    # plausible band and fall back to ATM IV outside it.
    calls["delta"] = [
        _bs_call_delta(spot, k, t_years, rate,
                       iv if iv and 0.10 <= iv <= 1.50 else result["atm_iv"])
        for k, iv in zip(calls.strike, calls.impliedVolatility)
    ]

    used = set()
    for target in ladder_deltas:
        row = calls.iloc[(calls.delta - target).abs().argsort()].iloc[0]
        if row.strike in used:
            continue
        used.add(row.strike)
        mid = round((row.bid + row.ask) / 2, 2) if row.ask > 0 else float(row.bid)
        prem_pct = mid / spot * 100
        result["ladder"].append({
            "strike": float(row.strike),
            "delta": float(row.delta),
            "otm_pct": (row.strike / spot - 1) * 100,
            "bid": float(row.bid),
            "ask": float(row.ask),
            "mid": mid,
            "iv": float(row.impliedVolatility) * 100,
            "oi": int(row.openInterest) if pd.notna(row.openInterest) else 0,
            "prem_pct": prem_pct,
            "ann_pct": prem_pct * 365 / dte,
        })
    result["ladder"].sort(key=lambda x: x["strike"])
    return result


def _format_ticker_section(r):
    lines = [f"### {r['ticker']} — {r['shares']} 股（可卖 {r['contracts']} 张）\n"]
    earn = r["earnings"].strftime("%Y-%m-%d") if r["earnings"] else "未知"
    iv_txt = f"{r['atm_iv']*100:.0f}%" if r["atm_iv"] else "-"
    vrp = (r["atm_iv"] - r["rv_trimmed"]) * 100 if r["atm_iv"] else None
    lines.append(
        f"现价 **${r['spot']:.2f}** | ATM IV {iv_txt} | 真实波动率(剔跳空) {r['rv_trimmed']*100:.0f}%"
        + (f" | 波动率溢价 {vrp:+.0f}pt" if vrp is not None else "")
        + f" | 下次财报 **{earn}**\n"
    )

    if r["status"] == "wait" or not r["ladder"]:
        lines.append("> ⚠️ **建议观望**：所有 25+ DTE 到期日均跨越财报（或期权数据不可用）。"
                     "等财报后 IV crush 落地 3-5 天再开新仓。\n")
        return "\n".join(lines)

    if vrp is not None and vrp < VRP_THIN_PT:
        lines.append(f"> ⚠️ **权利金偏薄**：IV 低于真实波动率 {abs(vrp):.0f}pt，卖方期望值不佳。"
                     "本期建议只覆盖下限张数，或跳过等 IV 回升。\n")

    dte_note = "（标准 45 天窗口）" if r["status"] == "ok" else "（财报临近，被迫缩短周期）"
    exp_date = datetime.strptime(r["expiry"], "%Y-%m-%d").date()
    roll_date = exp_date - timedelta(days=21)
    cover = (f"{r['cover_lo']} 张" if r["cover_lo"] == r["cover_hi"]
             else f"{r['cover_lo']}-{r['cover_hi']} 张")
    em_note = ""
    if r.get("exp_move"):
        em = r["exp_move"]
        em_note = (f" | 到期日 ±1σ 区间 **${r['spot']-em:.0f} – ${r['spot']+em:.0f}**"
                   f"（{em/r['spot']*100:.1f}%）")
    lines.append(
        f"**推荐到期日：{r['expiry']}（{r['dte']} DTE）**{dte_note} | "
        f"动量状态：{r['regime']} → 建议覆盖 **{cover}**{em_note}\n"
    )
    if "趋势强" in r["regime"]:
        lines.append("> 注：趋势股的实际涨穿率约为 delta 的 1.5-1.7 倍（经验校准）——"
                     "表中 0.20 delta 实际被行权概率按 ~30-35% 估计，阶梯已整体下移至 0.15-0.25 delta。\n")
    lines.append("| 行权价 | OTM | Delta | Bid/Ask | **挂单价(mid)** | 权利金/张 | 收益率 | 年化 | OI |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for l in r["ladder"]:
        lines.append(
            f"| ${l['strike']:.0f} | +{l['otm_pct']:.1f}% | {l['delta']:.2f} "
            f"| {l['bid']:.2f}/{l['ask']:.2f} | **${l['mid']:.2f}** | ${l['mid']*100:,.0f} "
            f"| {l['prem_pct']:.2f}% | {l['ann_pct']:.1f}% | {l['oi']:,} |"
        )
    lines.append("")
    if r["dte"] <= 24:
        lines.append("> 管理：本期周期短——成交后立即挂 GTC 买回单（限价 = 成交价 × 50%），"
                     "达标即离场，不设 21 DTE 滚动点；涨穿行权价则向上+向外滚动。\n")
    else:
        lines.append(f"> 管理：成交后立即挂 GTC 买回单（限价 = 成交价 × 50%）；"
                     f"未触发则 **{roll_date}**（剩21天）强制平仓滚动；"
                     f"涨穿行权价则向上+向外滚动，不要放到期。\n")
    return "\n".join(lines)


def build_covered_call_section(positions=None):
    """Markdown section for the daily report. Returns "" when no positions set."""
    if positions is None:
        positions = get_positions()
    if not positions:
        return ""

    rate = _risk_free_rate()
    parts = [
        "## Covered Call Advisor（持仓期权收租建议）\n",
        "*规则：45 DTE 开仓 / GTC 50% 止盈或 21 DTE 滚动 / 到期日必须在财报前 / "
        "趋势强则少覆盖+更远行权价（0.15-0.25Δ），磨底则多覆盖+更近（0.25-0.35Δ）。挂单用 mid 限价。*\n",
    ]
    for ticker, shares in positions.items():
        try:
            parts.append(_format_ticker_section(analyze_ticker(ticker, shares, rate)))
        except Exception as e:
            parts.append(f"### {ticker}\n\n*期权数据获取失败：{e}*\n")
    parts.append("---\n")
    return "\n".join(parts)


if __name__ == "__main__":
    os.environ.setdefault("CC_POSITIONS", "META:210,GOOGL:1000")
    print(build_covered_call_section())
