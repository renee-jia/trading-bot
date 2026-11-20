"""波段抄底 — TQQQ / SOXL swing desk (daily section of the report).

A rule-based dip-buying plan for the two 3x ETFs, for sharp declines and
choppy tape. Signals are read on the *unlevered* underlying (QQQ for TQQQ,
SOXX for SOXL) because 3x daily-reset products distort their own indicators:

1. 波段 (swing) state of the underlying: drawdown from the 52-week high,
   RSI(14), Bollinger %B, 5-day return, position vs 20/50/200-day averages.
2. 恐慌 (fear) state: VIX level, 1-year percentile, VIX/VIX3M term structure,
   and whether VIX has rolled over from its 10-day peak.
3. 情绪 (sentiment) context from the rest of the report: average news
   sentiment of the scored universe, breadth, credit and event window. These
   have no long history, so they can only shrink or delay an entry; they
   never create one.

The entry ladder (试探 / 加仓 / 恐慌) and the exit rules are deterministic and
identical in `tier_series` (history) and `build` (today), so the event study
printed in the section is the same rule that produced today's call. Sizes are
percentages of a separate swing sleeve; no dollar amount is ever written.

Everything is fail-safe: a missing series renders as unavailable.
"""
import math
from datetime import date

import numpy as np
import pandas as pd

# dd = underlying drawdown thresholds (%) for tiers 1/2/3; drop5 = 5-day flush.
PRODUCTS = {
    "TQQQ": {"under": "QQQ", "label": "3x 纳指 100", "dd": (6.0, 10.0, 16.0), "drop5": -4.0},
    "SOXL": {"under": "SOXX", "label": "3x 半导体", "dd": (10.0, 18.0, 28.0), "drop5": -7.0},
}
TIERS = {0: ("观望", 0), 1: ("试探仓", 25), 2: ("加仓", 60), 3: ("恐慌满仓", 100)}
VIX_T1, VIX_T2, VIX_T3 = 20.0, 25.0, 30.0
STOP_BELOW_LOW = 0.96        # exit when the underlying closes 4% under the episode low
TIME_STOP_DAYS = 15          # no 20-day reclaim after this many sessions -> halve
VIX_ROLLOVER = -5.0          # tiers 1-2 need VIX at least 5% off its 10-day closing high
MIN_HISTORY = 300


def _rsi(close, n=14):
    delta = close.diff()
    up = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    down = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / down.replace(0, np.nan))


def features(under, vix, vix3m=None):
    """Indicator frame on the underlying's index. All columns are point-in-time."""
    df = pd.DataFrame({"close": under}).dropna()
    df["vix"] = vix.reindex(df.index).ffill()
    df["vix3m"] = vix3m.reindex(df.index).ffill() if vix3m is not None else np.nan
    c = df["close"]
    df["dd"] = (c / c.rolling(252, min_periods=60).max() - 1) * 100
    df["rsi"] = _rsi(c)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    df["pctb"] = (c - (mid - 2 * sd)) / (4 * sd)
    df["sma20"], df["sma50"], df["sma200"] = mid, c.rolling(50).mean(), c.rolling(200).mean()
    df["upper"] = mid + 2 * sd
    df["ret5"] = (c / c.shift(5) - 1) * 100
    df["low10"] = c.rolling(10).min()
    df["vix_rank"] = df["vix"].rolling(252, min_periods=120).rank(pct=True) * 100
    df["vix_off_peak"] = (df["vix"] / df["vix"].rolling(10).max() - 1) * 100
    df["vix_ratio"] = df["vix"] / df["vix3m"]
    return df


def tier_series(f, product, confirmed=True):
    """Entry tier 0-3 for every day. Deeper tiers need deeper drawdown AND more fear.

    `confirmed=True` (the tradable signal) additionally requires VIX to have
    rolled over from its 10-day high for tiers 1-2: buying while fear is still
    making new highs was the main source of losses in the replay. Tier 3 is
    capitulation and needs no confirmation. `confirmed=False` returns the raw
    price/fear tier, used to say "price is there, waiting for confirmation".
    """
    cfg = PRODUCTS[product]
    d1, d2, d3 = cfg["dd"]
    oversold = (f["rsi"] < 35) | (f["pctb"] < 0.05) | (f["ret5"] <= cfg["drop5"])
    washed = (f["rsi"] < 40) | (f["pctb"] < 0.15)
    t1 = (f["dd"] <= -d1) & oversold & (f["vix"] >= VIX_T1)
    t2 = (f["dd"] <= -d2) & washed & ((f["vix"] >= VIX_T2) | (f["vix_rank"] >= 90))
    t3 = (f["dd"] <= -d3) & (f["vix"] >= VIX_T3)
    tier = pd.Series(0, index=f.index)
    tier[t1] = 1
    tier[t2] = 2
    if confirmed:
        tier[f["vix_off_peak"] > VIX_ROLLOVER] = 0
    tier[t3] = 3
    return tier


def simulate(f, etf, product):
    """Run the ladder + exit rules over history. Returns a list of closed episodes.

    One episode = flat -> first entry -> fully out. Fractions are of the swing
    sleeve; `ret` is the sleeve's return for the episode (cash earns zero).
    """
    tier = tier_series(f, product)
    px = etf.reindex(f.index).ffill()
    episodes, pos, cost, realised = [], 0.0, 0.0, 0.0
    start = low = None
    scaled = set()
    max_tier = days = 0
    worst = 0.0
    for i in range(MIN_HISTORY, len(f)):
        row, p = f.iloc[i], px.iloc[i]
        if not p or p != p:
            continue
        t = int(tier.iloc[i])
        if pos > 0:
            days += 1
            low = min(low, row["close"])
            worst = min(worst, (pos * p + realised) / cost - 1 if cost else 0)
            sell = 0.0
            if row["close"] < low_ref * STOP_BELOW_LOW and t < 3:
                sell, why = pos, "止损"
            elif "a" not in scaled and row["close"] > row["sma20"]:
                sell, why = pos / 3, "20日"
                scaled.add("a")
            elif "b" not in scaled and "a" in scaled and (row["close"] > row["sma50"] or row["rsi"] > 60):
                sell, why = pos / 2, "50日"
                scaled.add("b")
            elif "a" in scaled and (row["rsi"] > 70 or row["close"] > row["upper"]):
                sell, why = pos, "超买"
            elif days >= TIME_STOP_DAYS and "a" not in scaled and "t" not in scaled and t == 0:
                sell, why = pos / 2, "时间"
                scaled.add("t")
            if sell:
                realised += sell * p
                pos -= sell
                if pos < 1e-9:
                    episodes.append({"start": start, "end": f.index[i], "days": days, "max_tier": max_tier,
                                     "ret": realised / cost - 1, "invested": invested, "worst": worst,
                                     "exit": why, "bull": bull})
                    pos = cost = realised = 0.0
                    scaled, max_tier, days, worst = set(), 0, 0, 0.0
                    continue
        target = TIERS[t][1] / 100
        held_frac = invested if pos > 0 else 0.0
        if t > 0 and target > held_frac and (pos == 0 or t > max_tier):
            add = target - held_frac
            if pos == 0:
                start, low, days, invested = f.index[i], row["close"], 0, 0.0
                bull = bool(row["close"] > row["sma200"])
            pos += add / p
            cost += add
            invested = target
            max_tier = max(max_tier, t)
            low_ref = min(low, row["low10"]) if row["low10"] == row["low10"] else low
    return episodes


def episode_stats(episodes):
    if not episodes:
        return None
    r = np.array([e["ret"] * e["invested"] for e in episodes]) * 100      # % of the sleeve
    raw = np.array([e["ret"] for e in episodes]) * 100                    # % on capital used
    return {"n": len(episodes), "win": float((raw > 0).mean() * 100),
            "median": float(np.median(raw)), "mean": float(raw.mean()),
            "p10": float(np.quantile(raw, 0.10)), "worst": float(raw.min()),
            "avg_days": float(np.mean([e["days"] for e in episodes])),
            "worst_open": float(min(e["worst"] for e in episodes) * 100),
            "sleeve_mean": float(r.mean()),
            "first": str(episodes[0]["start"].date()), "last": str(episodes[-1]["end"].date())}


def forward_stats(f, etf, product, horizons=(5, 10, 20)):
    """Forward ETF returns after fresh tier triggers, vs every day."""
    tier = tier_series(f, product).iloc[MIN_HISTORY:]
    px = etf.reindex(f.index).ffill()
    out = {}
    for k in (1, 2, 3):
        fresh = (tier >= k) & (tier.shift(1) < k)
        days = tier.index[fresh.fillna(False)]
        row = {"n": len(days)}
        for h in horizons:
            fwd = (px.shift(-h) / px - 1).reindex(days).dropna() * 100
            if len(fwd):
                row[h] = {"median": float(fwd.median()), "win": float((fwd > 0).mean() * 100),
                          "p10": float(fwd.quantile(0.10))}
        out[k] = row
    base = {}
    for h in horizons:
        fwd = (px.shift(-h) / px - 1).iloc[MIN_HISTORY:].dropna() * 100
        base[h] = {"median": float(fwd.median()), "win": float((fwd > 0).mean() * 100),
                   "p10": float(fwd.quantile(0.10))}
    out["all"] = base
    return out


# ---------------------------------------------------------------------------
# Today's context (sentiment, breadth, credit, events) — can only shrink/delay
# ---------------------------------------------------------------------------

def sentiment_context(ranked, macro_result=None, regime=None):
    """News sentiment and breadth read from data the report already has."""
    scores, semis = [], []
    try:
        from configs import UNIVERSE
    except Exception:
        UNIVERSE = {}
    for r in ranked or []:
        comp = ((r.get("score_result") or {}).get("components") or {}).get("sentiment") or {}
        s, conf = comp.get("score"), comp.get("confidence")
        if s is None or (conf is not None and conf <= 0):
            continue
        scores.append(float(s))
        if (UNIVERSE.get(r.get("ticker"), {}) or {}).get("sector") in ("Semiconductors", "AI Data Storage"):
            semis.append(float(s))
    data = (regime or {}).get("data") or {}
    # Both products are tech/semis, so the scored pool's breadth is the closer
    # read; the S&P figure is shown beside it and used only when the pool is missing.
    breadth_sp = (data.get("breadth_sp500") or {}).get("above_50")
    breadth = (data.get("breadth_universe") or {}).get("above_50")
    if breadth is None:
        breadth = breadth_sp
    hy = ((data.get("fred") or {}).get("hy_oas") or {}).get("value")
    hits = [lab for lab, _c, _v, st in data.get("reduce_triggers") or [] if str(st).startswith("🔴")]
    return {"news": float(np.mean(scores)) if scores else None, "news_n": len(scores),
            "news_semis": float(np.mean(semis)) if len(semis) >= 3 else None,
            "breadth": breadth, "breadth_sp500": breadth_sp, "hy_oas": hy, "reduce_hits": hits,
            "risk_label": ((macro_result or {}).get("macro_tape") or {}).get("risk_label")}


def sentiment_label(ctx):
    """Contrarian read: washed-out sentiment helps a dip buy, euphoria hurts it."""
    news, breadth = ctx.get("news"), ctx.get("breadth")
    votes = []
    if news is not None:
        votes.append("fear" if news < 42 else "greed" if news > 62 else "mid")
    if breadth is not None:
        votes.append("fear" if breadth < 30 else "greed" if breadth > 65 else "mid")
    if not votes:
        return "unknown", "情绪数据不可用"
    if votes.count("fear") == len(votes):
        return "washed", "情绪已洗出（新闻偏空或广度很低）：逆向有利，但不能代替 VIX 回落确认"
    if "fear" in votes:
        return "cautious", "情绪偏谨慎，尚未到极端"
    if votes.count("greed") == len(votes):
        return "complacent", "情绪偏热：下跌可能还没被认真对待"
    return "neutral", "情绪中性，不提供逆向优势"


def next_major_event(regime, today=None):
    today = today or date.today()
    for iso, label, stars, _note in ((regime or {}).get("data") or {}).get("events") or []:
        try:
            day = iso if isinstance(iso, date) else date.fromisoformat(str(iso))
        except (TypeError, ValueError):
            continue
        if day >= today and int(stars) >= 3:
            return day, label, (day - today).days
    return None


def decide(product, row, tier, ctx, event=None, raw_tier=None):
    """Today's call for one product: tier from price/VIX, then context guards."""
    cfg = PRODUCTS[product]
    name, target = TIERS[tier]
    notes, cap = [], target
    raw_tier = tier if raw_tier is None else raw_tier
    if tier == 0 and raw_tier > 0:
        peak = row["vix"] / (1 + row["vix_off_peak"] / 100)
        need = peak * (1 + VIX_ROLLOVER / 100)
        return {"tier": 0, "raw_tier": raw_tier, "name": "等待确认", "target": 0, "cap": 0,
                "action": f"等待确认：价位已到第 {raw_tier} 档，VIX 还在升，先不买",
                "notes": [f"{cfg['under']} 回撤 {row['dd']:.1f}% 已满足第 {raw_tier} 档（{TIERS[raw_tier][0]}），"
                          f"但 VIX {row['vix']:.1f} 距 10 日高点仅 {row['vix_off_peak']:+.1f}%。"
                          f"确认条件：VIX 收盘 ≤ {need:.1f}（较 10 日高点回落 5%）后按该档买入；"
                          f"若先跌到第 3 档（回撤 ≤ −{cfg['dd'][2]:g}% 且 VIX ≥ {VIX_T3:g}）则无需确认"],
                "mood": sentiment_label(ctx)[1]}
    if tier == 0:
        gaps = []
        if row["dd"] > -cfg["dd"][0]:
            gaps.append(f"{cfg['under']} 回撤 {row['dd']:.1f}%，未到 −{cfg['dd'][0]:g}%")
        if row["vix"] < VIX_T1:
            gaps.append(f"VIX {row['vix']:.1f} < {VIX_T1:g}")
        if not gaps:
            gaps.append("回撤和 VIX 已到，但未超卖（RSI ≥ 35、%B ≥ 0.05、5 日跌幅不够）")
        return {"tier": 0, "raw_tier": 0, "name": name, "target": 0, "cap": 0, "action": "观望，不建仓",
                "notes": gaps, "mood": sentiment_label(ctx)[1]}
    if event and event[2] <= 1:
        cap = min(cap, TIERS[1][1])
        notes.append(f"{event[0].isoformat()} {event[1]} 就在眼前：事件落地前最多试探仓")
    if ctx.get("hy_oas") is not None and ctx["hy_oas"] > 4.5:
        cap = min(cap, TIERS[1][1])
        notes.append(f"高收益利差 {ctx['hy_oas']:.2f}% > 4.50%：信用在收紧，3x 产品最多试探仓")
    if len(ctx.get("reduce_hits") or []) >= 2:
        cap = min(cap, TIERS[1][1])
        notes.append("宏观减仓触发已命中两条以上：不加到试探仓以上")
    if row["close"] < row["sma200"]:
        cap = cap / 2
        notes.append(f"{cfg['under']} 在 200 日线下方：仓位减半（风险规则，回放样本不含 2008 年式熊市）")
    mood, mood_txt = sentiment_label(ctx)
    if mood == "complacent" and tier == 1:
        cap = min(cap, 15)
        notes.append("情绪偏热：试探仓再减小")
    action = (f"{name}：波段仓累计到 {cap:g}%" if cap == target
              else f"{name}（受限）：波段仓累计到 {cap:g}%，规则上限 {target}%")
    return {"tier": tier, "raw_tier": raw_tier, "name": name, "target": target, "cap": cap,
            "action": action, "notes": notes, "mood": mood_txt}


def exit_levels(product, row, etf_last):
    """Underlying levels for the exit ladder, with the approximate 3x ETF price."""
    u = row["close"]
    def etf_at(level):
        return etf_last * (1 + 3 * (level / u - 1)) if etf_last and level == level else None
    stop = min(u, row["low10"]) * STOP_BELOW_LOW
    out = [("止盈 1（卖 1/3）", "收盘站上 20 日线", row["sma20"]),
           ("止盈 2（再卖 1/3）", "收盘站上 50 日线，或 RSI > 60", row["sma50"]),
           ("止盈 3（清仓）", "RSI > 70 或突破布林上轨", row["upper"]),
           ("止损（清仓）", "收盘低于 10 日最低收盘价 4%（恐慌满仓档除外）", stop)]
    return [(lab, rule, lvl, (lvl / u - 1) * 100 if lvl == lvl else None, etf_at(lvl)) for lab, rule, lvl in out]


# ---------------------------------------------------------------------------
# Build + render
# ---------------------------------------------------------------------------

def _series(symbol, period="max"):
    import macro_regime
    return macro_regime._close_series(symbol, period=period)


def build(ranked=None, macro_result=None, regime=None, today=None, loader=_series):
    today = today or date.today()
    vix, vix3m = loader("^VIX"), loader("^VIX3M")
    ctx = sentiment_context(ranked, macro_result, regime)
    event = next_major_event(regime, today)
    out = {"as_of": None, "products": {}, "context": ctx, "event": event,
           "vix": None, "error": None}
    if vix is None:
        out["error"] = "VIX 行情不可用"
        return out
    for product, cfg in PRODUCTS.items():
        under, etf = loader(cfg["under"]), loader(product)
        if under is None or etf is None or len(under) < MIN_HISTORY + 60:
            out["products"][product] = {"error": f"{cfg['under']} / {product} 行情不可用"}
            continue
        f = features(under, vix, vix3m)
        row = f.iloc[-1].to_dict()
        tier = int(tier_series(f, product).iloc[-1])
        raw_tier = int(tier_series(f, product, confirmed=False).iloc[-1])
        etf_last = float(etf.iloc[-1])
        episodes = simulate(f, etf, product)
        out["as_of"] = str(f.index[-1].date())
        out["products"][product] = {
            "row": row, "etf_last": etf_last,
            "etf_dd": float((etf_last / etf.tail(252).max() - 1) * 100),
            "etf_ret5": float((etf_last / etf.iloc[-6] - 1) * 100) if len(etf) > 6 else None,
            "decision": decide(product, row, tier, ctx, event, raw_tier),
            "exits": exit_levels(product, row, etf_last),
            "stats": episode_stats(episodes),
            "stats_bear": episode_stats([e for e in episodes if not e["bull"]]),
            "forward": forward_stats(f, etf, product),
            "recent": [e for e in episodes[-3:]],
        }
    out["vix"] = {"last": float(vix.iloc[-1]),
                  "ratio": float(vix.iloc[-1] / vix3m.iloc[-1]) if vix3m is not None else None,
                  "rank": float((vix.tail(252) < vix.iloc[-1]).mean() * 100),
                  "off_peak": float((vix.iloc[-1] / vix.tail(10).max() - 1) * 100)}
    return out


def _f(value, fmt="{:+.1f}%"):
    return "—" if value is None or (isinstance(value, float) and math.isnan(value)) else fmt.format(value)


def _table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
                     + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]) + "\n"


def headline(data):
    """One line per product for the Executive Summary."""
    bits = []
    for product, p in (data or {}).get("products", {}).items():
        if p.get("error"):
            bits.append(f"{product} 数据不可用")
        else:
            bits.append(f"{product} {p['decision']['action']}")
    return "；".join(bits)


def build_section(data):
    L = ["## 波段抄底 — TQQQ / SOXL\n"]
    if not data or data.get("error") or not data.get("products"):
        L.append(f"*数据不可用：{(data or {}).get('error') or '未取得行情'}*\n")
        return "\n".join(L)
    v, ctx = data["vix"], data["context"]
    L.append("3 倍 ETF 只做**下跌后的波段**，不追涨、不长拿、不卖 put。信号看未加杠杆的标的"
             "（QQQ / SOXX），仓位是独立「波段仓」的累计百分比，与现金入场计划和动量组合分开。\n")
    mood, mood_txt = sentiment_label(ctx)
    fear = [f"VIX {v['last']:.1f}（1 年分位 {v['rank']:.0f}%）"]
    if v.get("ratio") is not None:
        fear.append(f"VIX/VIX3M {v['ratio']:.2f}{'（倒挂）' if v['ratio'] > 1 else ''}")
    fear.append(f"距 10 日高点 {v['off_peak']:+.1f}%")
    senti = []
    if ctx.get("news") is not None:
        senti.append(f"股票池新闻情绪均分 {ctx['news']:.0f}/100（{ctx['news_n']} 只）")
    if ctx.get("news_semis") is not None:
        senti.append(f"半导体 {ctx['news_semis']:.0f}")
    if ctx.get("breadth") is not None:
        senti.append(f"股票池 50 日线上方 {ctx['breadth']:.0f}%")
    if ctx.get("breadth_sp500") is not None:
        senti.append(f"标普 {ctx['breadth_sp500']:.0f}%")
    L.append(f"**恐慌读数：** {' · '.join(fear)}。**情绪：** {' · '.join(senti) or '—'} —— {mood_txt}。"
             f"（数据截至 {data.get('as_of') or '—'}）\n")

    rows = []
    for product, p in data["products"].items():
        cfg = PRODUCTS[product]
        if p.get("error"):
            rows.append([f"**{product}**", "—", "—", "—", "—", "—", p["error"]])
            continue
        r, d = p["row"], p["decision"]
        rows.append([f"**{product}**（{cfg['label']}）",
                     f"${p['etf_last']:.2f}（距高点 {_f(p['etf_dd'])}，5 日 {_f(p['etf_ret5'])}）",
                     f"{cfg['under']} 距高点 {_f(r['dd'])}、5 日 {_f(r['ret5'])}",
                     f"RSI {_f(r['rsi'], '{:.0f}')} · %B {_f(r['pctb'], '{:.2f}')}",
                     f"20 日 {_f((r['close'] / r['sma20'] - 1) * 100)} · 200 日 {_f((r['close'] / r['sma200'] - 1) * 100)}",
                     (f"第 {d['tier']} 档" if d["tier"] else
                      f"价位第 {d['raw_tier']} 档，待确认" if d.get("raw_tier") else "未触发"),
                     f"**{d['action']}**"])
    L.append(_table(["产品", "现价", "标的回撤", "超卖", "距均线", "档位", "今日结论"], rows))
    for product, p in data["products"].items():
        for note in (p.get("decision") or {}).get("notes") or []:
            L.append(f"- **{product}：** {note}")
    L.append("")

    L.append("### 入场阶梯（波段条件与恐慌条件同时满足才进该档）\n")
    ladder = []
    for product, cfg in PRODUCTS.items():
        d1, d2, d3 = cfg["dd"]
        u = cfg["under"]
        ladder += [
            [product, "1 试探仓", f"{u} 距高点 ≤ −{d1:g}% 且超卖（RSI<35 或 %B<0.05 或 5 日 ≤ {cfg['drop5']:g}%）", f"VIX ≥ {VIX_T1:g}，且已从 10 日高点回落 ≥5%", "25%"],
            [product, "2 加仓", f"{u} 距高点 ≤ −{d2:g}% 且 RSI<40 或 %B<0.15", f"VIX ≥ {VIX_T2:g} 或 1 年分位 ≥ 90%，且已回落 ≥5%", "60%"],
            [product, "3 恐慌满仓", f"{u} 距高点 ≤ −{d3:g}%", f"VIX ≥ {VIX_T3:g}（无需回落确认）", "100%"]]
    L.append(_table(["产品", "档位", "波段条件（标的）", "恐慌条件", "波段仓累计"], ladder))
    L.append("为什么等 VIX 回落：回放里在 VIX 仍创 10 日新高时买入是亏损的主要来源，"
             "加上这条确认后两只产品的胜率与最差一次都改善（同一段历史上比较得出，未做样本外验证）。"
             "减档规则：⭐⭐⭐ 事件前一天、高收益利差 > 4.50%、"
             "或宏观减仓触发命中两条以上 → 最多试探仓；标的在 200 日线下方 → 仓位减半；情绪偏热 → 试探仓再减。"
             "同一档只买一次，不在没有新档位的情况下摊平。\n")

    L.append("### 离场计划（按今日价位换算）\n")
    for product, p in data["products"].items():
        if p.get("error"):
            continue
        u = PRODUCTS[product]["under"]
        L.append(_table([f"{product} 规则", "条件", f"{u} 价位", "距现价", f"{product} 约"],
                        [[lab, rule, _f(lvl, "${:,.2f}"), _f(gap), _f(etf, "≈${:,.2f}")]
                         for lab, rule, lvl, gap, etf in p["exits"]]))
    L.append(f"时间止损：进场 {TIME_STOP_DAYS} 个交易日仍未站上 20 日线且档位已回到观望 → 先卖一半。"
             "3x ETF 价位按「3 × 标的涨跌」近似，震荡中有损耗，实际以标的条件为准。\n")

    L.append("### 历史验证（同一套规则回放）\n")
    stat_rows = []
    for product, p in data["products"].items():
        for label, s in (("全部", p.get("stats")), ("其中标的在 200 日线下方入场", p.get("stats_bear"))):
            if s:
                stat_rows.append([product, label, s["n"], f"{s['win']:.0f}%", _f(s["median"]), _f(s["mean"]),
                                  _f(s["p10"]), _f(s["worst"]), _f(s["worst_open"]), f"{s['avg_days']:.0f} 天"])
    if stat_rows:
        L.append(_table(["产品", "样本", "波段数", "胜率", "中位收益", "平均收益", "10% 分位", "最差一次",
                         "期间最深浮亏", "平均天数"], stat_rows))
        s = next((p["stats"] for p in data["products"].values() if p.get("stats")), None)
        L.append(f"收益按已投入资金计、收盘价成交、不含费用与滑点；回放区间 {s['first']} 至 {s['last']}。")
    fwd_rows = []
    for product, p in data["products"].items():
        fw = p.get("forward") or {}
        for key, label in ((1, "新触发 1 档"), (2, "新触发 2 档"), (3, "新触发 3 档"), ("all", "任意一天")):
            item = fw.get(key) or {}
            cells = []
            for h in (5, 10, 20):
                x = item.get(h)
                cells.append(f"{x['median']:+.1f}% / {x['win']:.0f}% / {x['p10']:+.1f}%" if x else "—")
            fwd_rows.append([product, label, item.get("n", "—") if key != "all" else "—"] + cells)
    if fwd_rows:
        L.append("\n触发后 3x ETF 的后续表现（中位收益 / 上涨占比 / 10% 分位）：\n")
        L.append(_table(["产品", "信号", "次数", "5 日后", "10 日后", "20 日后"], fwd_rows))
    L.append("样本少、且集中在少数几次下跌里，数字是规则的历史画像而不是胜率承诺；"
             "2022 年式的阴跌会连续止损，所以波段仓要小到全部亏完也不影响主组合。\n")
    L.append("> 3 倍 ETF 每日重置，震荡市有损耗，单日可跌 15–30%。本栏是规则化的波段计划，不是自动交易指令；"
             "仓位为波段仓百分比，金额自行换算。\n")
    return "\n".join(L)
