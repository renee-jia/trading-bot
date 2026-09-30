"""
Regime Desk — 宏观回撤与加仓时机 (daily section of the report).

Reproduces, every day, the 2026-09-29 macro study (docs/../reports/
scenario_analysis_macro_drawdown_20260929.md):

1. Quant dashboard computed in code (never by the LLM): tape vs highs and
   moving averages, S&P 500 and universe breadth, rates / real yield / credit
   spread / vol term structure, commodities and yen, historical-analog forward
   returns for today's state, the cash-entry ladder mapped to index levels,
   and a live reduce/add trigger checklist.
2. Upcoming catalysts from `macro_events.py` (hand-maintained; the section
   warns when the calendar is running out).
3. A Claude synthesis (claude-opus-5 with web search) that turns the dashboard
   plus the last few days of news into: regime label, drawdown windows with
   dates, scenario probabilities, entry / reduce views, top risks and sources.
   Falls back to a deterministic narrative when the key or the call is missing.

Everything is fail-safe: any data source that fails is shown as "—" and the
section still renders. Set MACRO_REGIME_LLM=0 to skip the Claude call and
MACRO_REGIME_BREADTH=0 to skip the ~500-name S&P download.
"""
import json
import math
import os
import re
from datetime import date, datetime

import numpy as np
import pandas as pd

import market_bars

SYNTH_MODEL = "claude-opus-5"
SYNTH_MAX_SEARCHES = 10

# yfinance symbols for the dashboard
TAPE = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq-100 (QQQ)", "RSP": "等权标普 (RSP)", "IWM": "罗素 2000 (IWM)"}
VOL = {"^VIX": "VIX", "^VIX3M": "VIX3M", "^MOVE": "MOVE", "^SKEW": "SKEW"}
RATES = {"^TNX": "10y", "^TYX": "30y", "^FVX": "5y", "^IRX": "3m"}
OTHER = {"GC=F": "黄金", "BZ=F": "Brent", "CL=F": "WTI", "DX-Y.NYB": "美元指数", "JPY=X": "USD/JPY", "HYG": "HYG", "LQD": "LQD"}
FRED = {"real10y": ("DFII10", "10y 实际利率"), "hy_oas": ("BAMLH0A0HYM2", "高收益利差")}

# Reduce / hedge triggers for the momentum book. (key, label, threshold text)
REDUCE_TRIGGERS = [
    ("qqq_below_200_narrow", "NDX 跌破 200 日线且广度 <20%", "QQQ < 200d 且 标普 50 日线上方占比 <20%"),
    ("tnx_54", "10 年期 >5.40%", "10y > 5.40%"),
    ("yen_carry", "日元两周升值 >5%（套利平仓）", "USD/JPY 10 日变动 < -5%"),
    ("brent_120", "Brent >120", "Brent > $120"),
    ("hy_450", "高收益利差 >450bp", "HY OAS > 4.50%"),
    ("vix_inverted", "VIX 期限结构倒挂", "VIX > VIX3M"),
]


# ---------------------------------------------------------------------------
# Data collection (all fail-safe)
# ---------------------------------------------------------------------------

def _close_series(ticker, period="2y"):
    """Daily close series (tz-naive, unfinished session dropped) or None."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if df is None or df.empty or "Close" not in df:
            return None
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy() if "Open" in df else df[["Close"]].copy()
        df.index = pd.DatetimeIndex([pd.Timestamp(x).tz_localize(None).normalize() for x in df.index])
        try:
            df = market_bars.drop_unfinished_bars(df)
        except Exception:
            pass
        s = df["Close"].dropna()
        return s if len(s) >= 20 else None
    except Exception:
        return None


def _fred_series(series_id):
    """Latest FRED observation as {value, date, change_5d} or None."""
    try:
        import requests
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        rows = [ln.split(",") for ln in r.text.strip().splitlines()[1:]]
        vals = [(d, float(v)) for d, v in rows if v not in (".", "")]
        if not vals:
            return None
        d, v = vals[-1]
        prev = vals[-6][1] if len(vals) >= 6 else None
        return {"value": v, "date": d, "change_5d": (v - prev) if prev is not None else None}
    except Exception:
        return None


def _pct(a, b):
    return (a / b - 1) * 100 if (a is not None and b not in (None, 0)) else None


def tape_stats(s):
    """From-high / vs-MA / returns for one close series."""
    if s is None or len(s) < 60:
        return None
    last = float(s.iloc[-1])
    out = {
        "last": last,
        "ret_5d": _pct(last, float(s.iloc[-6])) if len(s) > 6 else None,
        "ret_1m": _pct(last, float(s.iloc[-22])) if len(s) > 22 else None,
        "ret_3m": _pct(last, float(s.iloc[-64])) if len(s) > 64 else None,
        "from_high": _pct(last, float(s.tail(252).max())),
        "vs_sma50": _pct(last, float(s.tail(50).mean())),
        "vs_sma200": _pct(last, float(s.tail(200).mean())) if len(s) >= 200 else None,
    }
    return out


def breadth_from_frame(px):
    """Breadth stats for a wide close DataFrame (columns = tickers)."""
    if px is None or px.empty:
        return None
    px = px.dropna(axis=1, how="all")
    last = px.iloc[-1]
    ok = last.notna()
    if ok.sum() < 20:
        return None
    a50 = (last > px.tail(50).mean())[ok].mean() * 100
    a200 = (last > px.tail(200).mean())[ok].mean() * 100 if len(px) >= 200 else None
    from_hi = (last / px.tail(252).max() - 1)[ok]
    ret_1m = (last / px.iloc[-22] - 1)[ok] if len(px) > 22 else None
    return {
        "n": int(ok.sum()),
        "as_of": px.index[-1].date().isoformat(),
        "above_50": float(a50),
        "above_200": float(a200) if a200 is not None else None,
        "median_from_high": float(from_hi.median() * 100),
        "pct_20_below_high": float((from_hi < -0.2).mean() * 100),
        "median_ret_1m": float(ret_1m.median() * 100) if ret_1m is not None else None,
    }


def sp500_breadth(members=None, chunk=125):
    """Breadth over the S&P 500 member list (chunked batch download)."""
    if os.environ.get("MACRO_REGIME_BREADTH", "1") == "0":
        return None
    try:
        import yfinance as yf
        if members is None:
            from sp500_members import SP500_MEMBERS as members
        frames = []
        for i in range(0, len(members), chunk):
            part = members[i:i + chunk]
            try:
                df = yf.download(part, period="1y", interval="1d", auto_adjust=True,
                                 progress=False, threads=True, group_by="column")
            except Exception:
                continue
            if df is None or df.empty:
                continue
            closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]].rename(columns={"Close": part[0]})
            frames.append(closes)
        if not frames:
            return None
        px = pd.concat(frames, axis=1)
        px.index = pd.DatetimeIndex([pd.Timestamp(x).tz_localize(None).normalize() for x in px.index])
        px = px[~px.index.duplicated(keep="last")].sort_index()
        try:
            px = market_bars.drop_unfinished_bars(px)
        except Exception:
            pass
        return breadth_from_frame(px)
    except Exception:
        return None


def universe_breadth(scored_results):
    """Breadth of the scored universe from indicators already in memory."""
    rows = []
    for r in scored_results or []:
        ind = r.get("indicators") or {}
        p, s50, s200, fh = ind.get("price"), ind.get("sma_50"), ind.get("sma_200"), ind.get("pct_from_52w_high")
        if p is None:
            continue
        rows.append((p, s50, s200, fh, ind.get("change_1m")))
    if len(rows) < 10:
        return None
    a50 = [p > s for p, s, _, _, _ in rows if s]
    a200 = [p > s for p, _, s, _, _ in rows if s]
    fh = [x for _, _, _, x, _ in rows if x is not None]
    m1 = [x for _, _, _, _, x in rows if x is not None]
    return {
        "n": len(rows),
        "above_50": float(np.mean(a50) * 100) if a50 else None,
        "above_200": float(np.mean(a200) * 100) if a200 else None,
        "median_from_high": float(np.median(fh)) if fh else None,
        "pct_20_below_high": float(np.mean([x < -20 for x in fh]) * 100) if fh else None,
        "median_ret_1m": float(np.median(m1)) if m1 else None,
    }


# ---------------------------------------------------------------------------
# Historical analog
# ---------------------------------------------------------------------------

def _dd_bucket(dd):
    if dd > -3:
        return "距高点 <3%"
    if dd > -8:
        return "回撤 3–8%"
    if dd > -15:
        return "回撤 8–15%"
    return "回撤 >15%"


def _vix_bucket(v):
    if v < 17:
        return "VIX <17"
    if v < 25:
        return "VIX 17–25"
    return "VIX ≥25"


def _tnx_bucket(chg):
    if chg > 0.4:
        return "10y 三月内升 >40bp"
    if chg < -0.4:
        return "10y 三月内降 >40bp"
    return "10y 三月内持平"


def analog_stats(spy, vix, tnx):
    """Forward returns of SPY in past days that shared today's (dd, VIX, 10y-trend) buckets."""
    try:
        df = pd.DataFrame({"spy": spy, "vix": vix, "tnx": tnx}).dropna()
        df = df[df.index >= "1995-01-01"]
        if len(df) < 1500:
            return None
        dd = (df.spy / df.spy.rolling(252).max() - 1) * 100
        chg = df.tnx.diff(63)
        cur = {"dd": _dd_bucket(float(dd.iloc[-1])), "vix": _vix_bucket(float(df.vix.iloc[-1])),
               "tnx": _tnx_bucket(float(chg.iloc[-1]))}
        cond = (dd.map(_dd_bucket) == cur["dd"]) & (df.vix.map(_vix_bucket) == cur["vix"]) & (chg.map(_tnx_bucket) == cur["tnx"])
        cond = cond & (df.index < df.index[-1] - pd.Timedelta(days=380))  # need a full year forward
        if cond.sum() < 30:
            return {"state": cur, "n": int(cond.sum())}
        out = {"state": cur, "n": int(cond.sum()),
               "years": sorted(set(int(y) for y in df.index[cond].year))}
        for h, key in [(21, "1m"), (63, "3m"), (126, "6m"), (252, "12m")]:
            fwd = (df.spy.shift(-h) / df.spy - 1)[cond].dropna()
            out[f"fwd_{key}_median"] = float(fwd.median() * 100)
            out[f"fwd_{key}_hit"] = float((fwd > 0).mean() * 100)
        arr = df.spy.values
        idx = np.where(cond.values)[0]
        worst = []
        for i in idx:
            seg = arr[i:i + 126]
            worst.append(seg.min() / arr[i] - 1)
        out["worst_6m_dd_median"] = float(np.median(worst) * 100)
        out["worst_6m_dd_q25"] = float(np.quantile(worst, 0.25) * 100)
        return out
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ladder + triggers
# ---------------------------------------------------------------------------

def entry_ladder(spx_last, spx_peak, vix):
    """cash_entry_plan tiers mapped to index levels, with live hit status."""
    try:
        from cash_entry_plan import TIERS
    except Exception:
        TIERS = ()
    rows = []
    for name, min_vix, min_dd, target in TIERS:
        level = spx_peak * (1 - min_dd / 100) if spx_peak else None
        dd_hit = spx_last is not None and level is not None and spx_last <= level
        vix_hit = vix is not None and vix >= min_vix
        rows.append({"tier": name, "min_vix": min_vix, "min_dd": min_dd, "target_pct": target,
                     "level": level, "dd_hit": bool(dd_hit), "vix_hit": bool(vix_hit),
                     "hit": bool(dd_hit and vix_hit)})
    return rows


def _status(hit, near):
    return "🔴 触发" if hit else ("🟡 接近" if near else "🟢 未触发")


def reduce_triggers(d):
    """Live checklist rows: (label, condition, value text, status)."""
    q = d["tape"].get("QQQ") or {}
    b = d.get("breadth_sp500") or {}
    rates = d.get("rates") or {}
    other = d.get("other") or {}
    vol = d.get("vol") or {}
    rows = []
    # 1. QQQ below 200d and narrow breadth
    below = q.get("vs_sma200") is not None and q["vs_sma200"] < 0
    narrow = b.get("above_50") is not None and b["above_50"] < 20
    rows.append(("NDX 跌破 200 日线且广度 <20%", "QQQ<200d 且 标普 50 日线上方 <20%",
                 f"QQQ 距 200 日线 {q.get('vs_sma200'):+.1f}%，广度 {b.get('above_50'):.0f}%" if q.get("vs_sma200") is not None and b.get("above_50") is not None else "—",
                 _status(below and narrow, below or narrow)))
    t = rates.get("^TNX")
    rows.append(("10 年期 >5.40%", "10y > 5.40%", f"{t:.2f}%" if t is not None else "—",
                 _status(t is not None and t > 5.40, t is not None and t > 5.25)))
    jpy = other.get("JPY=X") or {}
    c10 = jpy.get("ret_10d")
    rows.append(("日元两周升值 >5%", "USD/JPY 10 日 < -5%", f"{c10:+.1f}%" if c10 is not None else "—",
                 _status(c10 is not None and c10 < -5, c10 is not None and c10 < -3)))
    br = (other.get("BZ=F") or {}).get("last")
    rows.append(("Brent >120", "Brent > $120", f"${br:.0f}" if br is not None else "—",
                 _status(br is not None and br > 120, br is not None and br > 105)))
    hy = (d.get("fred") or {}).get("hy_oas") or {}
    hv = hy.get("value")
    rows.append(("高收益利差 >450bp", "HY OAS > 4.50%", f"{hv:.2f}%" if hv is not None else "—",
                 _status(hv is not None and hv > 4.5, hv is not None and hv > 3.75)))
    v, v3 = vol.get("^VIX"), vol.get("^VIX3M")
    ratio = v / v3 if (v and v3) else None
    rows.append(("VIX 期限结构倒挂", "VIX > VIX3M", f"VIX/VIX3M {ratio:.2f}" if ratio else "—",
                 _status(ratio is not None and ratio > 1.0, ratio is not None and ratio > 0.95)))
    return rows


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def collect(scored_results=None, macro_result=None):
    d = {"as_of": None, "tape": {}, "vol": {}, "rates": {}, "other": {}, "fred": {}}
    series = {}
    for sym in list(TAPE) + list(VOL) + list(RATES) + list(OTHER):
        series[sym] = _close_series(sym, period="2y")
    for sym in TAPE:
        st = tape_stats(series[sym])
        if st:
            d["tape"][sym] = st
    if series.get("SPY") is not None:
        d["as_of"] = series["SPY"].index[-1].date().isoformat()
    for sym in VOL:
        s = series[sym]
        if s is not None:
            d["vol"][sym] = float(s.iloc[-1])
    v = series.get("^VIX")
    if v is not None and len(v) > 252:
        d["vol"]["vix_pct_rank_1y"] = float((v.tail(252) < v.iloc[-1]).mean() * 100)
        d["vol"]["vix_5d_ago"] = float(v.iloc[-6])
    for sym in RATES:
        s = series[sym]
        if s is not None:
            d["rates"][sym] = float(s.iloc[-1])
            d["rates"][sym + "_5d"] = float(s.iloc[-1] - s.iloc[-6]) if len(s) > 6 else None
            d["rates"][sym + "_3m"] = float(s.iloc[-1] - s.iloc[-64]) if len(s) > 64 else None
    for sym in OTHER:
        s = series[sym]
        if s is not None:
            last = float(s.iloc[-1])
            d["other"][sym] = {"last": last,
                               "ret_5d": _pct(last, float(s.iloc[-6])) if len(s) > 6 else None,
                               "ret_10d": _pct(last, float(s.iloc[-11])) if len(s) > 11 else None,
                               "ret_1m": _pct(last, float(s.iloc[-22])) if len(s) > 22 else None}
    hyg, lqd = series.get("HYG"), series.get("LQD")
    if hyg is not None and lqd is not None and len(hyg) > 22 and len(lqd) > 22:
        d["other"]["hyg_lqd_1m"] = _pct(float(hyg.iloc[-1] / lqd.iloc[-1]), float(hyg.iloc[-22] / lqd.iloc[-22]))
    for key, (sid, _) in FRED.items():
        f = _fred_series(sid)
        if f:
            d["fred"][key] = f
    d["breadth_sp500"] = sp500_breadth()
    d["breadth_universe"] = universe_breadth(scored_results)
    # analog on long history
    spy_l, vix_l, tnx_l = _close_series("SPY", "max"), _close_series("^VIX", "max"), _close_series("^TNX", "max")
    d["analog"] = analog_stats(spy_l, vix_l, tnx_l)
    # SPX level ladder: prefer the cash plan snapshot (index, not ETF)
    snap = ((macro_result or {}).get("cash_entry_plan") or {}).get("snapshot") or {}
    spx_last, spx_peak = snap.get("spx"), snap.get("peak")
    if not spx_last or not spx_peak:
        gspc = _close_series("^GSPC", "1y")
        if gspc is not None:
            spx_last, spx_peak = float(gspc.iloc[-1]), float(gspc.tail(252).max())
    d["spx"] = {"last": spx_last, "peak": spx_peak, "peak_date": snap.get("peak_date"),
                "drawdown": _pct(spx_last, spx_peak) if spx_last and spx_peak else None}
    d["ladder"] = entry_ladder(spx_last, spx_peak, d["vol"].get("^VIX"))
    d["cash_plan_tier"] = ((macro_result or {}).get("cash_entry_plan") or {}).get("tier")
    d["macro_score"] = (macro_result or {}).get("score")
    d["macro_tape"] = ((macro_result or {}).get("macro_tape") or {}).get("risk_label")
    d["reduce_triggers"] = reduce_triggers(d)
    try:
        import macro_events
        d["events"] = [(dd.isoformat(), label, stars, note) for dd, label, stars, note in macro_events.upcoming(8)]
        d["events_coverage_days"] = macro_events.coverage_days()
    except Exception:
        d["events"], d["events_coverage_days"] = [], 0
    return d


def build(scored_results=None, macro_result=None, api_key=None):
    data = collect(scored_results, macro_result)
    synthesis, source = None, "fallback"
    if os.environ.get("MACRO_REGIME_LLM", "1") != "0":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if key:
            try:
                synthesis = synthesize(data, key)
                source = SYNTH_MODEL if synthesis else "fallback"
            except Exception as e:  # never break the report
                print(f"  Regime Desk synthesis failed: {e}")
    print("LLMLOG " + json.dumps({"desk": "macro_regime", "source": source,
                                  "usage": (synthesis or {}).get("_usage")}, ensure_ascii=False))
    return {"data": data, "synthesis": synthesis, "source": source}


# ---------------------------------------------------------------------------
# Claude synthesis
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一家宏观对冲基金的资深策略师，每天为一位持有集中 AI/科技动量组合、并另有一笔待部署现金的投资者写"宏观回撤与加仓时机"简报。

你会收到：今天由代码算好的量化仪表盘（盘面、广度、利率/信用/波动、历史类比统计、现金入场阶梯、减仓触发表）和未来催化剂日历。量化数字以仪表盘为准，不要改写或编造数字。先用 web_search 做 3-5 次搜索（每次一个主题：Fed 与官员讲话、美债收益率与拍卖、油价/伊朗地缘、AI 资本开支与信用、大型科技财报/指引），只取最近 3 天的结果，再写结论。搜索结果里的日期要核对，过期的不要用。

只输出一个 JSON 对象（不要 markdown 围栏、不要多余文字），字段：
{
 "regime_label": "6-12 字的中文标签，例如 紧缩驱动的避险 + 窄广度",
 "regime_summary": "2-3 句中文：当前处于什么环境、指数表面与内部的差异、最主要的压力源",
 "drawdown_windows": [ {"window": "日期区间", "trigger": "触发因素", "likelihood": "高|中|低"} ],  // 按概率排序，2-3 条，含具体日期
 "scenarios": {
   "bull": {"prob": 整数, "line": "一句话：路径与最大回撤深度"},
   "base": {"prob": 整数, "line": "..."},
   "bear": {"prob": 整数, "line": "..."}
 },   // 三者相加 = 100
 "entry_view": "2-3 句：现在是不是加仓时机；最佳买点在哪一档、要等哪些确认；哪些情形下指数触发永远不会到（轮动）",
 "reduce_view": "1-2 句：动量仓位现在该持有/减仓/对冲，依据是哪条触发",
 "what_changed": "1-2 句：过去 1-3 天最重要的边际变化",
 "top_risks": ["风险 1", "风险 2", "风险 3"],
 "news": [ {"headline": "新闻一句话", "why": "为什么重要"} ],  // 3-5 条，来自搜索
 "sources": ["url1", "url2"]
}
规则：概率必须能自圆其说；回撤窗口必须落在日历里的真实事件上；如果搜索不到新信息，就在 what_changed 里说"无重大新增"；全部中文，数字保留原样。"""


def _data_block(d):
    """Compact, stable-ordered text of the dashboard for the prompt."""
    lines = [f"as_of: {d.get('as_of')}"]
    for sym, name in TAPE.items():
        t = d["tape"].get(sym)
        if t:
            lines.append(f"{name}: 距52周高 {t['from_high']:+.1f}%, 距200日 {t['vs_sma200']:+.1f}%, 距50日 {t['vs_sma50']:+.1f}%, 1月 {t['ret_1m']:+.1f}%, 5日 {t['ret_5d']:+.1f}%" if t.get("vs_sma200") is not None else f"{name}: 距52周高 {t['from_high']:+.1f}%")
    spx = d.get("spx") or {}
    if spx.get("last"):
        lines.append(f"SPX: {spx['last']:.0f}, 高点 {spx['peak']:.0f} ({spx.get('peak_date')}), 回撤 {spx['drawdown']:+.1f}%")
    for key, label in [("breadth_sp500", "标普500广度"), ("breadth_universe", "AI股票池广度")]:
        b = d.get(key)
        if b:
            lines.append(f"{label} (n={b['n']}): 50日线上方 {b['above_50']:.0f}%, 200日线上方 {b['above_200']:.0f}%, 中位数距52周高 {b['median_from_high']:+.1f}%, 跌超20%占比 {b['pct_20_below_high']:.0f}%, 1月中位 {b['median_ret_1m']:+.1f}%" if b.get("above_200") is not None and b.get("median_ret_1m") is not None else f"{label}: 50日线上方 {b.get('above_50')}")
    r = d.get("rates") or {}
    lines.append("利率: " + ", ".join(f"{RATES[k]} {r[k]:.2f}% (5日 {r.get(k+'_5d', 0) or 0:+.2f}, 3月 {r.get(k+'_3m', 0) or 0:+.2f})" for k in RATES if k in r))
    f = d.get("fred") or {}
    for key, (_, label) in FRED.items():
        if f.get(key):
            lines.append(f"{label}: {f[key]['value']:.2f}% ({f[key]['date']}, 5日 {f[key]['change_5d'] if f[key]['change_5d'] is not None else 0:+.2f})")
    v = d.get("vol") or {}
    lines.append("波动: " + ", ".join(f"{VOL[k]} {v[k]:.1f}" for k in VOL if k in v) + (f", VIX 1年分位 {v['vix_pct_rank_1y']:.0f}%" if "vix_pct_rank_1y" in v else ""))
    o = d.get("other") or {}
    lines.append("其他: " + ", ".join(f"{OTHER[k]} {o[k]['last']:.1f} (1月 {o[k]['ret_1m']:+.1f}%)" for k in OTHER if k in o and o[k].get("ret_1m") is not None))
    a = d.get("analog")
    if a and a.get("fwd_6m_median") is not None:
        lines.append(f"历史类比 [{a['state']['dd']} / {a['state']['vix']} / {a['state']['tnx']}] 1995年以来 {a['n']} 天: 之后1月中位 {a['fwd_1m_median']:+.1f}% (正 {a['fwd_1m_hit']:.0f}%), 3月 {a['fwd_3m_median']:+.1f}%, 6月 {a['fwd_6m_median']:+.1f}% (正 {a['fwd_6m_hit']:.0f}%), 12月 {a['fwd_12m_median']:+.1f}% (正 {a['fwd_12m_hit']:.0f}%); 未来6月最大回撤中位 {a['worst_6m_dd_median']:.1f}%, 25分位 {a['worst_6m_dd_q25']:.1f}%")
    lines.append("现金入场阶梯: " + "; ".join(f"{x['tier']} SPX≤{x['level']:.0f}(-{x['min_dd']}%) & VIX≥{x['min_vix']} → 累计{x['target_pct']:g}% [{'已触发' if x['hit'] else '未触发'}]" for x in d.get("ladder") or [] if x.get("level")))
    lines.append("减仓触发: " + "; ".join(f"{lab}: {val} {st}" for lab, _, val, st in d.get("reduce_triggers") or []))
    if d.get("macro_score") is not None:
        lines.append(f"仓库宏观分 {d['macro_score']:.0f}/100, 宏观带 {d.get('macro_tape')}, 现金计划档位 {d.get('cash_plan_tier')}")
    lines.append("未来催化剂: " + "; ".join(f"{dd} {'⭐'*int(st)} {lab}" for dd, lab, st, _ in d.get("events") or []))
    return "\n".join(lines)


def _extract_json(text):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    return json.loads(text[start:end + 1])


def synthesize(data, api_key):
    import anthropic
    _ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    client = anthropic.Anthropic(api_key=api_key, timeout=600.0, max_retries=1,
                                 default_headers={"anthropic-workspace-id": _ws} if _ws else None)
    today = datetime.now().strftime("%Y-%m-%d")
    user = f"今天是 {today}。\n\n## 量化仪表盘\n{_data_block(data)}\n\n先搜索最近 3 天的市场新闻，然后按系统提示的 JSON 格式输出。"
    resp = client.messages.create(
        model=SYNTH_MODEL,
        max_tokens=6000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": SYNTH_MAX_SEARCHES}],
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    out = _extract_json(text)
    if not isinstance(out, dict) or "regime_label" not in out:
        return None
    sc = out.get("scenarios") or {}
    tot = sum(int(sc.get(k, {}).get("prob", 0) or 0) for k in ("bull", "base", "bear"))
    out["_prob_total"] = tot
    stu = getattr(resp.usage, "server_tool_use", None)
    out["_usage"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens,
                     "searches": getattr(stu, "web_search_requests", None) if stu else None}
    return out


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _f(v, fmt="{:+.1f}%", dash="—"):
    return fmt.format(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else dash


def fallback_narrative(d):
    """Deterministic one-liners when the LLM synthesis is unavailable."""
    spx = d.get("spx") or {}
    b = d.get("breadth_sp500") or {}
    t = (d.get("rates") or {}).get("^TNX")
    v = (d.get("vol") or {}).get("^VIX")
    parts = []
    if spx.get("drawdown") is not None:
        parts.append(f"指数距高点 {spx['drawdown']:+.1f}%")
    if b.get("above_50") is not None:
        parts.append(f"标普 50 日线上方仅 {b['above_50']:.0f}%")
    if t is not None:
        parts.append(f"10 年期 {t:.2f}%")
    if v is not None:
        parts.append(f"VIX {v:.1f}")
    hits = [lab for lab, _, _, st in d.get("reduce_triggers") or [] if st.startswith("🔴")]
    nears = [lab for lab, _, _, st in d.get("reduce_triggers") or [] if st.startswith("🟡")]
    ladder_hit = [x["tier"] for x in d.get("ladder") or [] if x["hit"]]
    return {
        "regime_label": "（AI 综合不可用，仅量化）",
        "regime_summary": "；".join(parts) + "。" if parts else "数据不足。",
        "entry_view": (f"现金阶梯已触发：{'、'.join(ladder_hit)}。" if ladder_hit else "现金阶梯未触发任何档位，按计划等待。"),
        "reduce_view": (f"减仓触发已命中：{'、'.join(hits)}。" if hits else (f"接近触发：{'、'.join(nears)}。" if nears else "减仓触发均未命中，持有。")),
        "drawdown_windows": [{"window": dd, "trigger": lab, "likelihood": "—"} for dd, lab, st, _ in (d.get("events") or []) if int(st) >= 3][:3],
        "scenarios": None, "top_risks": [], "news": [], "sources": [], "what_changed": "",
    }


def build_section(result):
    d = result["data"]
    syn = result.get("synthesis") or fallback_narrative(d)
    src = result.get("source", "fallback")
    L = []
    L.append("## 宏观回撤与加仓时机 — Regime Desk\n")
    L.append(f"**环境：{syn.get('regime_label', '—')}**  |  数据截至 {d.get('as_of') or '—'}  |  综合来源：{src}\n")
    if syn.get("regime_summary"):
        L.append(syn["regime_summary"] + "\n")
    if syn.get("what_changed"):
        L.append(f"**边际变化：** {syn['what_changed']}\n")

    # Tape
    L.append("### 盘面与广度\n")
    L.append("| 指数 | 距 52 周高 | 距 200 日线 | 距 50 日线 | 1 月 | 5 日 |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for sym, name in TAPE.items():
        t = d["tape"].get(sym)
        if t:
            L.append(f"| {name} | {_f(t['from_high'])} | {_f(t['vs_sma200'])} | {_f(t['vs_sma50'])} | {_f(t['ret_1m'])} | {_f(t['ret_5d'])} |")
    for key, label in [("breadth_sp500", "标普 500"), ("breadth_universe", "AI 股票池")]:
        b = d.get(key)
        if b:
            L.append(f"\n**{label} 广度**（{b['n']} 只）：50 日线上方 {_f(b['above_50'], '{:.0f}%')}，200 日线上方 {_f(b.get('above_200'), '{:.0f}%')}，"
                     f"中位数距 52 周高 {_f(b.get('median_from_high'))}，跌超 20% 占比 {_f(b.get('pct_20_below_high'), '{:.0f}%')}，1 月中位 {_f(b.get('median_ret_1m'))}。")
    L.append("")

    # Rates / credit / vol
    L.append("### 利率 · 信用 · 波动\n")
    L.append("| 指标 | 数值 | 5 日变化 | 备注 |")
    L.append("|---|---:|---:|---|")
    r = d.get("rates") or {}
    for k, name in RATES.items():
        if k in r:
            note = "3 月 " + _f(r.get(k + "_3m"), "{:+.2f}pt") if r.get(k + "_3m") is not None else ""
            L.append(f"| 美债 {name} | {r[k]:.2f}% | {_f(r.get(k + '_5d'), '{:+.2f}pt')} | {note} |")
    f = d.get("fred") or {}
    for key, (_, label) in FRED.items():
        if f.get(key):
            L.append(f"| {label} | {f[key]['value']:.2f}% | {_f(f[key]['change_5d'], '{:+.2f}pt')} | FRED {f[key]['date']} |")
    v = d.get("vol") or {}
    if "^VIX" in v:
        ratio = (v["^VIX"] / v["^VIX3M"]) if v.get("^VIX3M") else None
        L.append(f"| VIX | {v['^VIX']:.1f} | {_f(v['^VIX'] - v['vix_5d_ago'], '{:+.1f}') if 'vix_5d_ago' in v else '—'} | 1 年分位 {_f(v.get('vix_pct_rank_1y'), '{:.0f}%')}；VIX/VIX3M {_f(ratio, '{:.2f}')}{'（倒挂）' if ratio and ratio > 1 else ''} |")
    for k in ("^MOVE", "^SKEW"):
        if k in v:
            L.append(f"| {VOL[k]} | {v[k]:.0f} | | |")
    o = d.get("other") or {}
    for k in ("BZ=F", "GC=F", "DX-Y.NYB", "JPY=X"):
        if k in o:
            L.append(f"| {OTHER[k]} | {o[k]['last']:.1f} | {_f(o[k].get('ret_5d'))} | 1 月 {_f(o[k].get('ret_1m'))} |")
    if o.get("hyg_lqd_1m") is not None:
        L.append(f"| HYG/LQD（信用相对） | | | 1 月 {_f(o['hyg_lqd_1m'], '{:+.2f}%')} |")
    L.append("")

    # Analog
    a = d.get("analog")
    if a and a.get("fwd_6m_median") is not None:
        L.append(f"**历史类比**（{a['state']['dd']} · {a['state']['vix']} · {a['state']['tnx']}，1995 年以来 {a['n']} 个交易日，"
                 f"出现在 {', '.join(str(y) for y in a['years'][-8:])}）：之后 1 月中位 {_f(a['fwd_1m_median'])}（正 {a['fwd_1m_hit']:.0f}%），"
                 f"6 月 {_f(a['fwd_6m_median'])}（正 {a['fwd_6m_hit']:.0f}%），12 月 {_f(a['fwd_12m_median'])}（正 {a['fwd_12m_hit']:.0f}%）；"
                 f"未来 6 月最大回撤中位 {a['worst_6m_dd_median']:.1f}%，25 分位 {a['worst_6m_dd_q25']:.1f}%。\n")

    # Drawdown windows + scenarios
    L.append("### 回撤窗口与情景\n")
    for w in syn.get("drawdown_windows") or []:
        L.append(f"- **{w.get('window', '')}** — {w.get('trigger', '')}（概率：{w.get('likelihood', '—')}）")
    sc = syn.get("scenarios")
    if sc:
        L.append("\n| 情景 | 概率 | 路径 |")
        L.append("|---|---:|---|")
        for k, name in [("bull", "乐观"), ("base", "基准"), ("bear", "悲观")]:
            s = sc.get(k) or {}
            L.append(f"| {name} | {s.get('prob', '—')}% | {s.get('line', '')} |")
    L.append("")

    # Entry ladder
    spx = d.get("spx") or {}
    L.append("### 加仓阶梯（现金池累计投入目标）\n")
    if spx.get("peak"):
        L.append(f"指数高点 {spx['peak']:,.0f}（{spx.get('peak_date') or '52 周'}），现价 {spx['last']:,.0f}，回撤 {_f(spx.get('drawdown'))}。\n")
    L.append("| 档位 | 指数条件 | VIX | 累计投入 | 状态 |")
    L.append("|---|---|---:|---:|---|")
    for x in d.get("ladder") or []:
        st = "✅ 已触发" if x["hit"] else ("🟡 指数已到，等 VIX" if x["dd_hit"] else ("🟡 VIX 已到，等指数" if x["vix_hit"] else "⏳ 未到"))
        L.append(f"| {x['tier']} | ≤{x['level']:,.0f}（-{x['min_dd']}%） | ≥{x['min_vix']} | {x['target_pct']:g}% | {st} |")
    if syn.get("entry_view"):
        L.append(f"\n**加仓判断：** {syn['entry_view']}\n")

    # Reduce triggers
    L.append("### 动量仓位减仓/对冲触发\n")
    L.append("| 触发 | 条件 | 当前 | 状态 |")
    L.append("|---|---|---|---|")
    for lab, cond, val, st in d.get("reduce_triggers") or []:
        L.append(f"| {lab} | {cond} | {val} | {st} |")
    if syn.get("reduce_view"):
        L.append(f"\n**仓位判断：** {syn['reduce_view']}\n")

    # Risks + news
    if syn.get("top_risks"):
        L.append("**主要风险：** " + "；".join(syn["top_risks"]) + "\n")
    if syn.get("news"):
        L.append("**近 3 日要闻：**")
        for n in syn["news"][:5]:
            L.append(f"- {n.get('headline', '')} — {n.get('why', '')}")
        L.append("")

    # Events
    L.append("### 未来催化剂\n")
    L.append("| 日期 | 事件 | 影响 | 备注 |")
    L.append("|---|---|---|---|")
    for dd, lab, st, note in d.get("events") or []:
        L.append(f"| {dd} | {lab} | {'⭐' * int(st)} | {note} |")
    cov = d.get("events_coverage_days", 0)
    try:
        import macro_events
        if cov < macro_events.MIN_FORWARD_DAYS:
            L.append(f"\n⚠️ 事件日历仅剩 {cov} 天覆盖，请更新 `macro_events.py`。")
    except Exception:
        pass
    if syn.get("sources"):
        L.append("\n<small>来源：" + " · ".join(f"[{i+1}]({u})" for i, u in enumerate(syn["sources"][:8])) + "</small>")
    L.append("\n> 量化数字由代码计算；情景概率与窗口为 AI 综合判断，不是预测。阶梯为现金池比例，非交易指令。\n\n---\n")
    return "\n".join(L)
