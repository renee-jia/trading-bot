"""Macro-first briefing — the report's opening section.

Every report starts by judging the macro factors and the macro situation, and
only then moves to stocks and options. This module is presentation plus
deterministic judgment: it never fetches and never calls an LLM. Inputs are
`macro_analyzer.analyze_macro()`'s result and the Regime Desk snapshot
(`macro_regime.build()`); every reading falls back to the macro tape when the
Regime Desk is unavailable, and a missing reading is shown as ⚪ (no vote)
rather than guessed.

The headline stance is the existing index stance (`trend_outlook`); the factor
table explains it and never overrides it.
"""
from datetime import date

ICON = {"good": "🟢 顺风", "watch": "🟡 留意", "bad": "🔴 逆风", "na": "⚪ 缺数据"}

STOCK_ACTIONS = {
    "scale_in": "分批买，不打满",
    "hold": "指数持有不追；个股仍按「今日建议买入」逐只判断",
    "trim": "减高 beta，停止新开仓",
}
OPTION_ACTIONS = {
    "wait": "先别开新空头，等波动率定价完",
    "caution": "少张数、远月，或等数据落地后再写",
    "no_new_short": "今天不开新的短 put / 短 call",
    "sell_call_ok": "强势股可以写 call，不买短 call",
    "selective": "按统一方向分、波动估值、流动性与事件窗口筛选",
}


def _n(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value != value else value


def _pct(value, digits=1):
    value = _n(value)
    return "—" if value is None else f"{value:+.{digits}f}%"


def _lvl(value, digits=2, suffix=""):
    value = _n(value)
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _worst(*signals):
    order = ("bad", "watch", "good", "na")
    return next(s for s in order if s in signals)


def _factor(name, reading, signal, judgment, implication):
    return {"name": name, "reading": reading, "signal": signal,
            "judgment": judgment, "implication": implication}


def _trend_factor(macro, data):
    trend = macro.get("trend") or {}
    spy = (data.get("tape") or {}).get("SPY") or {}
    phase = trend.get("phase")
    if not phase:
        return _factor("指数趋势", "—", "na", "趋势数据不可用", "按个股信号执行，仓位从小")
    reading = (f"SPY 距高点 {_pct(trend.get('spy_from_high'))}、距 200 日线 {_pct(spy.get('vs_sma200'))}；"
               f"QQQ 距高点 {_pct(trend.get('qqq_from_high'))}；SMH 距高点 {_pct(trend.get('smh_from_high'))}")
    signal = {"uptrend": "good", "pullback": "watch", "range": "watch",
              "correction": "bad", "bear_market": "bad"}.get(phase, "watch")
    implication = {
        "uptrend": "趋势未坏，持有为主，加仓等回撤",
        "pullback": "上升趋势内的回撤：分批，不追跌也不追涨",
        "range": "没有单边趋势，降低交易频率",
        "correction": "调整区间：按现金阶梯分批，不一次打满",
        "bear_market": "先降风险预算，反弹当减仓窗口",
    }.get(phase, "按趋势栏执行")
    judgment = f"{trend.get('label', '—')} · {trend.get('structure_label', '—')}"
    if trend.get("structure") in ("internal_rotation", "growth_underperforms"):
        signal = _worst(signal, "watch")
        implication += "；成长/芯片弱于指数，指数稳不代表它们安全"
    below_200 = trend.get("above_sma200") is False or (_n(spy.get("vs_sma200")) is not None and spy["vs_sma200"] < 0)
    if below_200 and phase in ("pullback", "range", "uptrend"):
        signal, judgment = "bad", judgment + "；标普已跌破 200 日线"
        implication = "回撤在升级：不追跌、不抢反弹，新钱只按现金阶梯和波段规则分批"
    qqq = _n(trend.get("qqq_from_high"))
    if qqq is not None and qqq <= -10 and phase not in ("correction", "bear_market"):
        signal = _worst(signal, "watch")
        judgment += f"；纳指已进入{'熊市' if qqq <= -20 else '调整'}区间（{qqq:+.1f}%）"
    return _factor("指数趋势", reading, signal, judgment, implication)


def _breadth_factor(macro, data):
    sp, uni = data.get("breadth_sp500") or {}, data.get("breadth_universe") or {}
    b, label = (sp, "标普 500") if sp.get("above_50") is not None else (uni, "股票池")
    a50 = _n(b.get("above_50"))
    if a50 is None:
        return _factor("市场广度", "—", "na", "广度数据不可用", "用个股评分与趋势代替广度判断")
    reading = (f"{label}（{b.get('n', '—')} 只）50 日线上方 {a50:.0f}%、200 日线上方 "
               f"{_lvl(b.get('above_200'), 0, '%')}、跌超 20% 占比 {_lvl(b.get('pct_20_below_high'), 0, '%')}")
    if label == "标普 500" and uni.get("above_50") is not None:
        reading += f"；股票池 50 日线上方 {uni['above_50']:.0f}%"
    spy_dd = _n((macro.get("trend") or {}).get("spy_from_high"))
    if a50 >= 60:
        signal, judgment, implication = "good", "多数个股在中期均线上方，上涨有参与度", "可以正常选股分批"
    elif a50 >= 40:
        signal, judgment, implication = "watch", "参与度一般，选股比指数重要", "只加评分与趋势同时过线的名字"
    else:
        signal, judgment, implication = "bad", "多数个股已跌破中期均线", "新开仓只选强势与质量，回避破位股"
    if a50 < 45 and spy_dd is not None and spy_dd > -5:
        judgment = "指数强、内部弱（窄广度），由少数权重撑着"
    if a50 < 20:
        implication = "广度已到极端：不追杀，等企稳信号后才是分批买点"
    return _factor("市场广度", reading, signal, judgment, implication)


def _rates_factor(macro, data):
    tape, rates = macro.get("macro_tape") or {}, data.get("rates") or {}
    tnx = _n(rates.get("^TNX")) if rates.get("^TNX") is not None else _n(tape.get("tnx"))
    if tnx is None:
        return _factor("利率与曲线", "—", "na", "利率数据不可用", "不据此调整仓位")
    change = (f"5 日 {rates['^TNX_5d']:+.2f}pt" if _n(rates.get("^TNX_5d")) is not None
              else f"5 日 {_pct(tape.get('tnx_5d'))}（收益率水平变化）")
    tyx = rates.get("^TYX") if rates.get("^TYX") is not None else tape.get("tyx")
    irx = rates.get("^IRX") if rates.get("^IRX") is not None else tape.get("irx")
    reading = f"10Y {tnx:.2f}%（{change}）· 30Y {_lvl(tyx, 2, '%')} · 3M {_lvl(irx, 2, '%')}"
    if _n(irx) is not None:
        curve = tnx - _n(irx)
        state = "倒挂" if curve < 0 else ("平坦" if curve < 0.30 else "正常")
        reading += f" · 10Y−3M {curve:+.2f}pt（{state}）"
    state = tape.get("rates")
    signal = {"tighter": "bad", "easier": "good", "stable_tight": "watch"}.get(state, "watch")
    implication = {
        "tighter": "估值承压：先停加高 beta 和长久期成长",
        "easier": "利率回落，成长股估值有支撑",
        "stable_tight": "利率高位但不再上行：估值不再被压，也没有顺风",
    }.get(state, "利率方向不明，不据此加减仓")
    if tnx > 5.25:
        signal, implication = "bad", "10 年期逼近 5.40% 减仓触发线，停止加长久期成长"
    return _factor("利率与曲线", reading, signal, tape.get("rates_label") or "—", implication)


def _credit_factor(macro, data):
    fred, tape = data.get("fred") or {}, macro.get("macro_tape") or {}
    real, hy = fred.get("real10y") or {}, fred.get("hy_oas") or {}
    hyv, hyg = _n(hy.get("value")), _n(tape.get("hyg_5d"))
    if hyv is None and hyg is None:
        return _factor("实际利率与信用", "—", "na", "信用数据不可用", "不据此调整仓位")
    bits = []
    if _n(real.get("value")) is not None:
        bits.append(f"10Y 实际利率 {real['value']:.2f}%"
                    + (f"（5 日 {real['change_5d']:+.2f}pt）" if _n(real.get("change_5d")) is not None else ""))
    if hyv is not None:
        bits.append(f"高收益利差 {hyv:.2f}%"
                    + (f"（5 日 {hy['change_5d']:+.2f}pt）" if _n(hy.get("change_5d")) is not None else ""))
    if hyg is not None:
        bits.append(f"HYG 5 日 {hyg:+.1f}%")
    if hyv is not None and hyv > 4.5:
        signal = "bad"
    elif (hyv is not None and hyv > 3.75) or (hyg is not None and hyg <= -1):
        signal = "bad" if (hyv is None and hyg <= -1) else "watch"
    else:
        signal = "good"
    judgment = {"good": "信用没有收紧，尚无系统性压力",
                "watch": "利差在走阔，留意是否扩散",
                "bad": "信用在收紧：回撤可能从杀估值变成杀基本面"}[signal]
    if hyv is None:
        judgment += "（利差数据缺失，仅看 HYG）"
    implication = {"good": "回撤按估值/情绪性质处理，可以分批",
                   "watch": "加仓放慢一档，回避高负债与小盘",
                   "bad": "停止抄底高 beta，先等利差见顶"}[signal]
    return _factor("实际利率与信用", " · ".join(bits), signal, judgment, implication)


def _vol_factor(macro, data):
    tape, vol = macro.get("macro_tape") or {}, data.get("vol") or {}
    vix = _n(vol.get("^VIX")) if vol.get("^VIX") is not None else _n(tape.get("vix"))
    if vix is None:
        return _factor("波动率", "—", "na", "波动率数据不可用", "期权先不开新仓")
    ratio = vix / vol["^VIX3M"] if _n(vol.get("^VIX3M")) else None
    reading = f"VIX {vix:.1f}"
    if _n(vol.get("vix_pct_rank_1y")) is not None:
        reading += f"（1 年分位 {vol['vix_pct_rank_1y']:.0f}%）"
    if ratio is not None:
        reading += f" · VIX/VIX3M {ratio:.2f}"
    if _n(vol.get("^MOVE")) is not None:
        reading += f" · MOVE {vol['^MOVE']:.0f}"
    state = tape.get("vol") or ("complacent" if vix < 13 else "calm" if vix < 18 else
                                "elevated" if vix < 25 else "stress" if vix < 28 else "panic")
    signal = {"calm": "good", "complacent": "watch", "elevated": "watch",
              "stress": "bad", "panic": "bad"}.get(state, "watch")
    implication = {
        "calm": "可按纪律写权利金，股票正常分批",
        "complacent": "市场太安静，数据日容易补波动；不卖太短的权利金",
        "elevated": "仓位减小、分批拉长；权利金变厚，但先让事件过去",
        "stress": "不追杀；等波动率回落再加",
        "panic": "不追杀；恐慌叠加指数双位数回撤才是现金阶梯的买点",
    }.get(state, "按 Options Desk 执行")
    judgment = tape.get("vol_label") or state
    if ratio is not None and ratio > 1:
        signal, judgment = "bad", judgment + "，期限结构倒挂（近端恐慌）"
    return _factor("波动率", reading, signal, judgment, implication)


def _fx_factor(macro, data):
    tape, other = macro.get("macro_tape") or {}, data.get("other") or {}
    dxy, jpy = other.get("DX-Y.NYB") or {}, other.get("JPY=X") or {}
    bits = []
    if _n(dxy.get("last")) is not None:
        bits.append(f"美元指数 {dxy['last']:.1f}（5 日 {_pct(dxy.get('ret_5d'))}）")
    elif _n(tape.get("uup_5d")) is not None:
        bits.append(f"UUP 5 日 {_pct(tape.get('uup_5d'))}")
    yen = _n(jpy.get("ret_10d"))
    if _n(jpy.get("last")) is not None:
        bits.append(f"USD/JPY {jpy['last']:.1f}（10 日 {_pct(yen)}）")
    if not bits:
        return _factor("美元与日元", "—", "na", "汇率数据不可用", "不据此调整仓位")
    dollar = tape.get("dollar")
    signal, judgment, implication = "good", tape.get("dollar_label") or "美元平稳", "汇率不构成压力"
    if dollar == "strong":
        signal = "bad" if tape.get("rates") == "tighter" else "watch"
        implication = "美元走强压海外收入与流动性，成长股先挨打"
    elif dollar == "weak":
        implication = "美元走弱利好风险资产与海外收入"
    if yen is not None and yen < -5:
        signal, judgment = "bad", judgment + "；日元急升，套利平仓风险"
        implication = "套利平仓会无差别抛售高 beta，先降杠杆"
    elif yen is not None and yen < -3:
        signal, judgment = _worst(signal, "watch"), judgment + "；日元在升，留意套利平仓"
    return _factor("美元与日元", " · ".join(bits), signal, judgment, implication)


def _commodity_factor(macro, data):
    tape, other = macro.get("macro_tape") or {}, data.get("other") or {}
    brent, gold = other.get("BZ=F") or {}, other.get("GC=F") or {}
    bits = []
    if _n(brent.get("last")) is not None:
        bits.append(f"Brent ${brent['last']:.0f}（5 日 {_pct(brent.get('ret_5d'))}、1 月 {_pct(brent.get('ret_1m'))}）")
    elif _n(tape.get("oil_5d")) is not None:
        bits.append(f"油 5 日 {_pct(tape.get('oil_5d'))}")
    if _n(gold.get("last")) is not None:
        bits.append(f"黄金 ${gold['last']:,.0f}（1 月 {_pct(gold.get('ret_1m'))}）")
    elif _n(tape.get("gld_5d")) is not None:
        bits.append(f"金 5 日 {_pct(tape.get('gld_5d'))}")
    if not bits:
        return _factor("油价与黄金", "—", "na", "商品数据不可用", "不据此调整仓位")
    level = _n(brent.get("last"))
    oil_5d = _n(brent.get("ret_5d")) if brent else _n(tape.get("oil_5d"))
    if level is not None and level > 120:
        signal, judgment, implication = "bad", "油价已构成通胀冲击", "通胀预期上修压利率，减高估值成长"
    elif (level is not None and level > 105) or (oil_5d is not None and oil_5d >= 8):
        signal, judgment, implication = "watch", "油价在抬升通胀预期", "留意是否传导到利率；能源可作对冲"
    else:
        signal, judgment, implication = "good", "油价未构成通胀冲击", "商品不构成压力"
    return _factor("油价与黄金", " · ".join(bits), signal, judgment, implication)


def _event_factor(macro, data, today, option_event):
    events = []
    for iso, label, stars, _note in data.get("events") or []:
        try:
            day = iso if isinstance(iso, date) else date.fromisoformat(str(iso))
            events.append((day, label, int(stars)))
        except (TypeError, ValueError):
            continue
    events = [e for e in events if e[0] >= today]
    stale = "；⚠️ 期权事件日历超出已核验范围，期权新开仓暂停" if (option_event or {}).get("status") == "unknown" else ""
    if not events:
        return _factor("事件窗口", "事件日历无未来条目", "na", "日历需要更新" + stale,
                       (option_event or {}).get("reason") or "事件前后仓位从小")
    first = events[0]
    major = next((e for e in events if e[2] >= 3), None)
    reading = f"最近：{first[0].isoformat()} {first[1]}（{(first[0] - today).days} 天）"
    if major and major != first:
        reading += f"；下一个 ⭐⭐⭐：{major[0].isoformat()} {major[1]}（{(major[0] - today).days} 天）"
    days = (major[0] - today).days if major else None
    if days is not None and days <= 1:
        signal, judgment = "bad", "⭐⭐⭐ 事件就在眼前"
        implication = "今天不开新的方向性仓位和短权利金，等结果落地"
    elif days is not None and days <= 5:
        signal, judgment = "watch", "一周内有 ⭐⭐⭐ 事件"
        implication = "新开仓缩小一档，期权只做远月、少张数"
    else:
        signal, judgment, implication = "good", "近期没有 ⭐⭐⭐ 事件挡路", "可以按信号正常执行"
    if _n(data.get("events_coverage_days")) is not None and data["events_coverage_days"] < 14:
        judgment += "；⚠️ 催化剂日历即将用完"
    return _factor("事件窗口", reading, signal, judgment + stale, implication)


def factors(macro_result, regime, today=None, option_event=None):
    """Eight macro factors, each with a reading, a signal, a judgment and a sizing implication."""
    macro, data = macro_result or {}, (regime or {}).get("data") or {}
    today = today or date.today()
    return [_trend_factor(macro, data), _breadth_factor(macro, data), _rates_factor(macro, data),
            _credit_factor(macro, data), _vol_factor(macro, data), _fx_factor(macro, data),
            _commodity_factor(macro, data), _event_factor(macro, data, today, option_event)]


def verdict(macro_result, regime, rows):
    """Headline numbers shared by the macro section and the Executive Summary."""
    macro, data = macro_result or {}, (regime or {}).get("data") or {}
    count = {k: sum(r["signal"] == k for r in rows) for k in ICON}
    bad = [r["name"] for r in rows if r["signal"] == "bad"]
    watch = [r["name"] for r in rows if r["signal"] == "watch"]
    if len(bad) >= 3:
        temperature = "逆风为主"
    elif bad or len(watch) >= 3:
        temperature = "有压力点"
    elif count["good"] >= 4:
        temperature = "环境友好"
    else:
        temperature = "信号不足"
    outlook, trend, tape = macro.get("trend_outlook") or {}, macro.get("trend") or {}, macro.get("macro_tape") or {}
    triggers = data.get("reduce_triggers") or []
    return {
        "stance_label": outlook.get("stance_label") or "—",
        "stance": outlook.get("stance"),
        "phase_label": trend.get("label") or "—",
        "risk_label": tape.get("risk_label") or "—",
        "score": _n(macro.get("score")),
        "recommendation": macro.get("recommendation") or "",
        "temperature": temperature, "count": count, "bad": bad, "watch": watch,
        "triggered": [lab for lab, _c, _v, st in triggers if str(st).startswith("🔴")],
        "near": [lab for lab, _c, _v, st in triggers if str(st).startswith("🟡")],
        "regime_label": ((regime or {}).get("synthesis") or {}).get("regime_label"),
    }


def one_line(v):
    score = f"宏观分 {v['score']:.0f}/100" if v.get("score") is not None else "宏观分 —"
    pressure = "、".join(v["bad"] + v["watch"]) or "无"
    return (f"{v['stance_label']}（{v['phase_label']}，{v['risk_label']}，{score}）；"
            f"因素 {v['temperature']}：🟢 {v['count']['good']} · 🟡 {v['count']['watch']} · 🔴 {v['count']['bad']}，"
            f"压力点：{pressure}")


def _table(headers, rows):
    def cell(value):
        return str(value if value not in (None, "") else "—").replace("|", r"\|").replace("\n", " ")
    return "\n".join(["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
                     + ["| " + " | ".join(map(cell, row)) + " |" for row in rows]) + "\n"


def _split_regime(regime_section):
    """(judgment blocks for the main body, raw tables for the appendix, analog line).

    The factor table already carries the key readings, so the Regime Desk's raw
    tape and rates tables are evidence; its scenarios, triggers, risks, news and
    catalysts are judgment and stay in the main body.
    """
    text = (regime_section or "").strip()
    if not text:
        return "", "", ""
    if "\n### " not in text:
        body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("## ")).strip()
        return (f"### Regime Desk\n\n{body}\n" if body else ""), "", ""
    blocks = ["### " + b.strip() for b in text.split("\n### ")[1:]]
    main, raw, analog = [], [], ""
    for block in blocks:
        if block.startswith(("### 盘面与广度", "### 利率")):
            kept = []
            for line in block.splitlines():
                if line.startswith("**历史类比**"):
                    analog = line.strip()
                else:
                    kept.append(line)
            raw.append("\n".join(kept).strip())
        elif block.startswith("### 回撤窗口") and "| 情景 |" not in block and "概率：—）" in block:
            continue  # no synthesis: these are just the catalyst rows again
        else:
            main.append(block)
    return "\n\n".join(main) + "\n", "\n\n".join(raw) + ("\n" if raw else ""), analog


def outlook_block(macro, heading="### 趋势与期限展望"):
    """Index trend narrative and 1w/1m/6m outlook from the daily assessment."""
    outlook = (macro or {}).get("trend_outlook") or {}
    labels = {"up": "偏多", "down": "偏空", "range": "震荡"}
    rows = [[name, labels.get((outlook.get(key) or {}).get("direction"), "震荡"),
             (outlook.get(key) or {}).get("view") or "—"]
            for name, key in (("未来一周", "outlook_1w"), ("未来一个月", "outlook_1m"), ("未来半年", "outlook_6m"))
            if outlook.get(key)]
    lines = [heading + "\n"]
    if outlook.get("current_trend"):
        lines.append(outlook["current_trend"] + "\n")
    if outlook.get("sizing_guidance"):
        lines.append(f"**仓位说明：** {outlook['sizing_guidance']}\n")
    if rows:
        lines.append(_table(["期限", "方向", "判断"], rows))
    return "\n".join(lines) if len(lines) > 1 else ""


def _narrative(macro):
    """Bull/bear logic from the daily macro assessment (LLM), when present."""
    a = macro.get("analysis") or {}
    if not a:
        return ""
    lines = ["### 多空逻辑与催化剂\n"]
    if a.get("summary"):
        lines.append(f"{a['summary']}\n")
    if a.get("action_guidance"):
        lines.append(f"**今日指引：** {a['action_guidance']}\n")
    if a.get("bull_case"):
        lines.append(f"**多头逻辑：** {a['bull_case']}\n")
    if a.get("bear_case"):
        lines.append(f"**空头逻辑：** {a['bear_case']}\n")
    for title, key in (("正面催化剂", "key_catalysts"), ("主要风险", "key_risks"),
                       ("驱动今日判断的新闻", "cited_news")):
        items = [str(x) for x in a.get(key) or [] if x]
        if items:
            lines.append(f"**{title}：**\n\n" + "\n".join("- " + x for x in items) + "\n")
    return "\n".join(lines) if len(lines) > 1 else ""


def build(macro_result, regime=None, regime_section="", today=None, option_event=None,
          cash_reserve_pct=None, error=None):
    """Return (markdown, verdict dict). Safe with partial or missing inputs.

    The section is the macro *situation*: stance headline, environment, factor
    table, scenarios, triggers and catalysts. What to do about it is listed
    once, in the Executive Summary, from the verdict returned here. Raw Regime
    Desk tables are returned in verdict["evidence"] for the appendix.
    """
    macro = macro_result or {}
    rows = factors(macro, regime, today=today, option_event=option_event)
    v = verdict(macro, regime, rows)
    data = (regime or {}).get("data") or {}
    syn = (regime or {}).get("synthesis") or {}
    tape, plan = macro.get("macro_tape") or {}, macro.get("cash_entry_plan") or {}
    searched = bool(syn.get("regime_summary"))
    main_blocks, raw_blocks, analog = _split_regime(regime_section)

    score = f"{v['score']:.0f}/100" if v["score"] is not None else "—"
    parts = [x for x in (v["recommendation"],
                         f"量化 {macro['quant_score']:.0f}" if _n(macro.get("quant_score")) is not None else "",
                         f"AI {macro['claude_score']:.0f}" if _n(macro.get("claude_score")) is not None else "") if x]
    lines = ["## 宏观研判 — 先看宏观，再看个股\n",
             f"**宏观结论：{v['stance_label']}** · {v['phase_label']} · {v['risk_label']} · "
             f"宏观分 {score}" + (f"（{' / '.join(parts)}）" if parts else "") + "\n",
             f"**因素温度：{v['temperature']}** — 🟢 顺风 {v['count']['good']} · 🟡 留意 {v['count']['watch']} · "
             f"🔴 逆风 {v['count']['bad']}" + (f" · ⚪ 缺数据 {v['count']['na']}" if v["count"]["na"] else "")
             + (f"。逆风：{'、'.join(v['bad'])}" if v["bad"] else "")
             + (f"。留意：{'、'.join(v['watch'])}" if v["watch"] else "") + "。\n"]
    if error:
        lines.append(f"*Regime Desk 数据不可用（{error}）；下表改用宏观带读数，缺失项标 ⚪。*\n")
    if syn.get("regime_label") or searched:
        lines.append(f"**环境：{syn.get('regime_label', '—')}**（数据截至 {data.get('as_of') or '—'}）"
                     f" {syn.get('regime_summary') or ''}\n")
    elif tape.get("implication"):
        lines.append(f"**宏观带读数：** {tape['implication']}\n")
    if syn.get("what_changed"):
        lines.append(f"**边际变化：** {syn['what_changed']}\n")
    lines.append(_table(["宏观因素", "当前读数", "信号", "判断", "对仓位的含义"],
                        [[f"**{r['name']}**", r["reading"], ICON[r["signal"]], r["judgment"], r["implication"]]
                         for r in rows]))
    if analog:
        lines.append(analog + "\n")
    if syn.get("entry_view"):
        lines.append(f"**加仓时机：** {syn['entry_view']}\n")
    if main_blocks.strip():
        lines.append(main_blocks)
    # Without a searched synthesis the daily assessment is the only narrative,
    # so it stays here; with one, it would be a second, unsearched story and
    # goes to the appendix instead.
    if not searched:
        for block in (outlook_block(macro), _narrative(macro)):
            if block:
                lines.append(block)
    lines.append("信号口径：🟢 不构成压力或有利，🟡 需留意，🔴 逆风，⚪ 数据缺失不计票。"
                 "因素表解释总仓位结论，不单独触发交易；原始行情表在宏观附录。\n")

    # Action lines, rendered once by the Executive Summary.
    v["stock_line"] = STOCK_ACTIONS.get(tape.get("stock_action"), "按趋势与评分逐只执行")
    v["options_line"] = OPTION_ACTIONS.get(tape.get("options_action"), "按 Options Desk 执行")
    v["cash_reserve_pct"] = cash_reserve_pct
    # The index stance, the tape and the cash ladder are separate rule sets and
    # can disagree in a sell-off. Say so instead of leaving the reader to guess.
    defensive = v.get("stance") in ("trim", "aggressive_sell")
    buying = tape.get("stock_action") == "scale_in" or (_n(plan.get("target_pct")) or 0) > 0
    v["divergence"] = bool(defensive and buying)
    v["searched"] = searched
    v["evidence"] = raw_blocks
    return "\n".join(lines), v
