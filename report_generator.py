"""
Report generator for stock recommendations.

Generates decision-first Markdown and HTML reports with a merged theme watch
book, one scoring table, and expandable evidence appendices and stock details.
"""
from datetime import datetime
from pathlib import Path


def _preclose_data_note(data_as_of):
    """Morning runs score the previous completed session, not the open print."""
    import market_bars
    now = datetime.now(market_bars.NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=market_bars.NY)
    now = now.astimezone(market_bars.NY)
    if now.time() >= market_bars.SESSION_CLOSE:
        return ""
    return (f"\n\n本次在美股收盘前生成：日线、评分与纸面交易信号截至 "
            f"{data_as_of}，不含今天未完成的K线。")


def generate_report(scored_results, output_dir="reports", macro_result=None,
                    discovery_result=None, options_decisions=None,
                    sell_put_plan=None, ai_portfolio=None, saas_watch=None,
                    dip_layout=None, chip_layout=None, optics_layout=None,
                    power_layout=None):
    """
    Generate a comprehensive Markdown recommendation report.

    Args:
        scored_results: list of dicts, each with keys:
            ticker, name, sector, score_result, indicators
        output_dir: directory to write report to
        macro_result: dict from macro_analyzer.analyze_macro() (optional)
        discovery_result: dict from stock_discovery.discover_stocks() (optional)
        sell_put_plan: dict from ai_sell_put_plan.build_plan() (optional; built
            here when omitted so the AI sell-put desk always renders)
        ai_portfolio: optional dict that receives ai_portfolio.build() output so
            the caller can reuse it for the email digest
        saas_watch: optional dict that receives saas_watch.build() output, same idea
        dip_layout: optional dict that receives dip_layout.build() output, same idea
        chip_layout: optional dict that receives chip_layout.build() output, same idea
        optics_layout: optional dict that receives optics_layout.build() output, same idea
        power_layout: optional dict that receives power_layout.build() output, same idea

    Returns:
        (report_path, report_content) tuple
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"{output_dir}/recommendations_{timestamp}.md"

    # Sort by score
    import stock_signals
    ranked = sorted(stock_signals.add_peer_ranks(scored_results),
                    key=lambda x: x["score_result"]["score"], reverse=True)

    import json
    import options_research
    try:
        options_section, snapshots = options_research.build_section(ranked, macro_result)
    except Exception:
        options_section = "## Options Research — 期权链信号\n\n数据不可用，本次跳过期权研究。\n"
        snapshots = []
    snapshot_path = Path(output_dir) / f"options_research_{timestamp}.json"
    snapshot_path.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2, allow_nan=False))
    decisions = {r['ticker']:r['decision'] for r in snapshots if 'decision' in r}
    if options_decisions is not None:
        options_decisions.update(decisions)
    ranked = [{**r, 'options_decision':decisions.get(r['ticker'],
               {'status':'wait','action':'wait','reasons':['本次期权链未评估或数据不可用']})} for r in ranked]
    if sell_put_plan is None:
        try:
            import ai_sell_put_plan
            sell_put_plan = ai_sell_put_plan.build_plan(ranked, macro_result=macro_result)
        except Exception as e:
            sell_put_plan = {"error": str(e)}
    if sell_put_plan and not sell_put_plan.get("error"):
        plan_path = Path(output_dir) / f"ai_sell_put_plan_{timestamp}.json"
        plan_path.write_text(json.dumps(sell_put_plan, ensure_ascii=False, indent=2, default=str))
    try:
        import ai_portfolio as ai_portfolio_desk
        portfolio = ai_portfolio_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        portfolio = {"error": str(e)}
    if ai_portfolio is not None:
        ai_portfolio.update(portfolio)
    try:
        import saas_watch as saas_desk
        saas = saas_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        saas = {"error": str(e)}
    if saas_watch is not None:
        saas_watch.update(saas)
    try:
        import dip_layout as dip_desk
        layout = dip_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        layout = {"error": str(e)}
    if dip_layout is not None:
        dip_layout.update(layout)
    try:
        import chip_layout as chip_desk
        chips = chip_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        chips = {"error": str(e)}
    if chip_layout is not None:
        chip_layout.update(chips)
    try:
        import optics_layout as optics_desk
        optics = optics_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        optics = {"error": str(e)}
    if optics_layout is not None:
        optics_layout.update(optics)
    try:
        import power_layout as power_desk
        power = power_desk.build(
            ranked, macro_result=macro_result,
            sell_put_plan=None if (sell_put_plan or {}).get("error") else sell_put_plan)
    except Exception as e:
        power = {"error": str(e)}
    if power_layout is not None:
        power_layout.update(power)
    report = _build_report(ranked, macro_result=macro_result,
                          discovery_result=discovery_result,
                          options_research_section=options_section,
                          sell_put_plan=sell_put_plan, ai_portfolio=portfolio,
                          saas_watch=saas, dip_layout=layout, chip_layout=chips,
                          optics_layout=optics, power_layout=power)

    Path(report_path).write_text(report, encoding="utf-8")
    from report_format import render_html
    Path(report_path).with_suffix(".html").write_text(render_html(report), encoding="utf-8")

    return report_path, report


def _build_report(ranked, macro_result=None, discovery_result=None, options_research_section="",
                  sell_put_plan=None, ai_portfolio=None, saas_watch=None,
                  dip_layout=None, chip_layout=None, optics_layout=None,
                  power_layout=None):
    """Decision desks first; deduplicated watch book and evidence appendices."""
    import daily_watch
    import macro_regime
    from options_decision import render_tickets
    from report_sections import subsection, remove_subsection, theme_section, unique, table

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_as_of = max((r.get("data_as_of") or r.get("price_as_of") for r in ranked
                      if r.get("data_as_of") or r.get("price_as_of")), default="未知")
    counts = {rec: sum(r["score_result"]["recommendation"] == rec for r in ranked)
              for rec in ("Strong Buy", "Buy", "Hold", "Reduce", "Avoid")}
    overview = " · ".join(f"{rec} {count}" for rec, count in counts.items())
    header = f"""# Trading Bot · 每日策略报告

**生成时间：** {now}（本机时区） · **数据截至：** {data_as_of}（完整日线） · **分析股票：** {len(ranked)} 只{_preclose_data_note(data_as_of)}
"""
    # 1. Macro first: judge the macro factors and situation before any stock.
    import macro_brief
    option_event = {}
    try:
        option_event = daily_watch.options_event_status()
    except Exception:
        pass
    cash_reserve = None
    try:
        import strategy as _strategy
        cash_reserve = _strategy.macro_cash_pct((macro_result or {}).get("score", 50)) * 100
    except Exception:
        pass
    regime, regime_section, regime_error = None, "", None
    try:
        regime = macro_regime.build(ranked, macro_result=macro_result)
        # The cash plan owns the entry ladder. Keep the other regime evidence.
        regime_section = remove_subsection(macro_regime.build_section(regime), "加仓阶梯")
    except Exception as e:
        regime_error = str(e)
    try:
        macro_section, verdict = macro_brief.build(
            macro_result, regime, regime_section, option_event=option_event,
            cash_reserve_pct=cash_reserve, error=regime_error)
    except Exception as e:
        macro_section, verdict = f"## 宏观研判 — 先看宏观，再看个股\n\n*数据不可用：{e}*\n", None

    trend_section = _build_stock_trend_section(ranked, macro_result)
    try:
        buy_section = daily_watch.build_buy_section(ranked, macro_result=macro_result, standalone=False)
        options_desk = daily_watch.build_options_desk_section(ranked, macro_result=macro_result, compact=True)
    except Exception as e:
        buy_section = f"## 今日建议买入\n\n*数据不可用：{e}*\n"
        options_desk = f"## Options Desk — 今日期权操作\n\n*数据不可用：{e}*\n"
    decisions = {r['ticker']: r['options_decision'] for r in ranked if r.get('options_decision') is not None}
    tickets = render_tickets(decisions)
    options_desk += ("\n### 候选合约与风险\n\n" + tickets if tickets else "")
    # The watch-list put plan answers every name daily; its summary table is a
    # decision aid, so it sits in the desk and only per-name detail is appendix.
    put_summary, put_detail = "", ""
    try:
        import ai_sell_put_plan
        if sell_put_plan is None:
            sell_put_plan = ai_sell_put_plan.build_plan(ranked, macro_result=macro_result)
        if sell_put_plan.get("error"):
            raise RuntimeError(sell_put_plan["error"])
        put_text = ai_sell_put_plan.build_section(sell_put_plan)
        head, sep, rest = put_text.partition('\n### ')
        put_summary = _put_plan_summary(sell_put_plan) or subsection(head)
        put_detail = ("### AI 标的 Sell Put 方案 — 逐只明细\n\n" + subsection("### " + rest)) if sep else ""
    except Exception as e:
        put_detail = f"### AI 标的 Sell Put 方案\n\n*数据不可用：{e}*\n"
    if put_summary:
        options_desk += "\n" + put_summary

    swing, swing_section = None, ""
    try:
        import leveraged_swing
        swing = leveraged_swing.build(ranked, macro_result=macro_result, regime=regime)
        swing_section = leveraged_swing.build_section(swing)
    except Exception as e:
        swing_section = f"## 波段抄底 — TQQQ / SOXL\n\n*数据不可用：{e}*\n"

    # Books are already built by generate_report; support direct/offline callers too.
    books = []
    for label, module, data in (("AI", "ai_portfolio", ai_portfolio),
                                ("半导体", "chip_layout", chip_layout),
                                ("光学互联", "optics_layout", optics_layout),
                                ("SaaS", "saas_watch", saas_watch),
                                ("抄底布局", "dip_layout", dip_layout),
                                ("电力", "power_layout", power_layout)):
        if data is None:
            try:
                import importlib
                data = importlib.import_module(module).build(ranked, macro_result=macro_result,
                                                              sell_put_plan=sell_put_plan)
            except Exception as e:
                data = {"error": str(e)}
        books.append((label, data))
    try:
        cards, _ = daily_watch._load_cards(ranked, macro_result=macro_result)
    except Exception:
        cards = []
    # Each fact has one home: macro situation -> conclusions -> cash plan ->
    # buy desk -> one table for every scored name -> swing -> options -> fixed
    # theme views -> allocation. No second ranking, movers or leaders table.
    sections = [header, macro_section,
                _build_executive_summary(ranked, counts, macro_result, verdict, cards, option_event,
                                         decisions, books, swing),
                trend_section, buy_section, _build_stock_table(ranked, macro_result, books),
                swing_section, options_desk, theme_section(books, reference_only=True),
                _build_portfolio_weights(ranked, macro_result), _build_alpaca_performance()]
    if discovery_result and discovery_result.get("report"):
        sections.append(discovery_result["report"])

    # Distinct chain research and watch-list ladders live together in one appendix.
    # Unified decision tickets are rendered exactly once in the Options Desk above.
    # Ticket headings are also level 3, so remove the entire trailing ticket
    # area rather than stopping at the first contract heading.
    research = options_research_section.split('\n### 操作评分与候选合约', 1)[0]
    # Preserve the research definitions/limitations that follow the tickets.
    if '\nIV÷RV 是' in options_research_section:
        research += '\nIV÷RV 是' + options_research_section.split('\nIV÷RV 是', 1)[1]
    option_parts = [subsection(research)]
    if put_detail:
        option_parts.append(put_detail)
    try:
        import covered_call_advisor
        option_parts.append(subsection(covered_call_advisor.build_covered_call_ladders()))
    except Exception as e:
        option_parts.append(f"### Covered Call 观察阶梯\n\n*数据不可用：{e}*\n")
    try:
        import sell_put_advisor
        option_parts.append(subsection(sell_put_advisor.build_sell_put_radar(ranked)))
    except Exception as e:
        option_parts.append(f"### Sell Put 雷达\n\n*数据不可用：{e}*\n")
    sections.append("## 附录 — 期权数据与观察阶梯\n\n名单方案与雷达是参考筛选，不能代替正文的统一期权评分。\n\n" +
                    "\n".join(option_parts))
    searched = bool((verdict or {}).get("searched"))
    macro_parts = ["宏观判断与因素表在报告开头；这里是原始行情表、指数快照与新闻。\n"]
    if (verdict or {}).get("evidence"):
        macro_parts.append(subsection("## Regime Desk 原始读数\n\n" + verdict["evidence"]))
    if searched:
        # The daily assessment model has no web access. Its text is kept as
        # evidence of what drove the macro score, not as a second narrative.
        macro_parts.append("### 每日宏观评分模型的原始输出\n\n该模型不联网，涉及政策方向的表述以开头的环境判断"
                           "（联网检索）为准。\n\n" + subsection(macro_brief.outlook_block(macro_result, "### 趋势与期限展望")))
    macro_parts.append(subsection(_build_macro_section(macro_result, evidence_only=not searched)))
    sections.append("## 附录 — 宏观依据与新闻\n\n" + "\n".join(macro_parts))

    # One ranking table replaces separate top/bottom lists and component tables.
    rows = []
    add_map = _add_card_map(ranked, macro_result)
    for i, r in enumerate(ranked, 1):
        sr, ind = r['score_result'], r.get('indicators') or {}
        components = sr.get('components') or {}
        breakdown = ' / '.join(_fmt_num((components.get(k) or {}).get('score'))
                               for k in ('technical', 'trend', 'alpha', 'sentiment'))
        rows.append([i, r['ticker'], f"{sr['score']:.1f}", f"{sr.get('confidence', 0) * 100:.0f}%", breakdown])
    sections.append("## 附录 — 评分总表与研究\n\n价格、涨跌与评级见正文「个股总表」；这里是评分分项。顺序：技术 / 趋势 / Alpha / 情绪。\n\n" +
                    table(['排名', '股票', '评分', '置信度', '评分分项'], rows) +
                    "\n" + subsection(_build_score_research(ranked)))
    import strategy
    mom_desc = '6 个月动量，跳过最近 1 个月' if strategy.MOM_MODE == '6_1' else '70% 三月 + 30% 一月动量'
    sections.append(f"""## 附录 — 方法与口径

综合评分：技术 35% + 趋势 30% + Alpha 20% + 新闻情绪 15%，按置信度与市场环境调整。
评级：75+ Strong Buy；60–74 Buy；45–59 Hold；30–44 Reduce；低于 30 Avoid。

组合按 {mom_desc} 选前 {strategy.TOP_N} 只，95% 动量排名 + 5% 评分权重；
单股上限 {strategy.MAX_POS:g}%，宏观现金预留 0–20%，组合年化波动目标 {strategy.VOL_TARGET*100:g}%。
行情历史不足时，动量回退到三月/一月；波动控制结果见建议配置栏目。
实盘另有持仓排名缓冲与交易检查；建议配置与现金入场计划使用不同资金口径。
历史回测与限制见 docs/BACKTEST_RESULTS.md，候选研究分不改变评级或下单。
""")
    details = ["## Detailed Analysis\n"]
    for r in ranked:
        sr, ind = r['score_result'], r.get('indicators') or {}
        card = _row_add(r, add_map)
        details.extend([f"### {r['ticker']} - {r.get('name', r['ticker'])}\n",
                        f"**评分：** {sr['score']:.1f}/100（{sr.get('grade', '—')}） · {sr['recommendation']} · "
                        f"置信度 {sr.get('confidence', 0)*100:.0f}% · {r.get('sector', 'Unknown')}\n",
                        f"价格 {_fmt_money(ind.get('price'))} · 1日 {_fmt_pct(ind.get('change_1d'))} · "
                        f"5日 {_fmt_pct(ind.get('change_5d'))} · 1月 {_fmt_pct(ind.get('change_1m'))} · "
                        f"3月 {_fmt_pct(ind.get('change_3m'))} · RSI {_fmt_num(ind.get('rsi'))}\n"
                        f"距高点 {_fmt_pct(card.get('from_high'))} · 距50日 {_fmt_pct(card.get('from_sma50'))} · "
                        f"距200日 {_fmt_pct(card.get('from_sma200'))} · {card.get('add_verdict') or '—'}\n"])
        if card.get('stock_note'):
            details.append(f"**涨跌结构：** {card.get('stock_label') or '—'}。{card['stock_note']}\n")
        confidence_weights = []
        for label, component in (sr.get('components') or {}).items():
            conf = component.get('confidence')
            weight = component.get('weight')
            confidence_weights.append(f"{label} " +
                                      ('—' if conf is None else f'{conf*100:.0f}%') + ' / ' +
                                      ('—' if weight is None else f'{weight*100:.0f}%'))
        if confidence_weights:
            details.append('**分项置信度 / 权重：** ' + '；'.join(confidence_weights) + '\n')
        synth = (sr.get('trend_detail') or {}).get('claude_synthesis') or {}
        factors = unique(list(sr.get('reasoning') or []) +
                         [synth.get(k) for k in ('regime_assessment', 'momentum_assessment', 'risk_assessment', 'outlook')])
        if factors:
            details.append('**判断依据：**\n\n' + '\n'.join('- ' + f for f in factors) + '\n')
        details.append(_compact_stock_news(r))
    sections.append('\n'.join(details))
    sections.append("## Disclaimer\n\n仅供研究参考。历史表现不保证未来收益；下单前核验实时价格、事件与资金条件。\n")
    from report_format import format_markdown
    return format_markdown('\n\n'.join(s for s in sections if s))


def _build_macro_section(macro_result, evidence_only=False):
    """Daily macro assessment. `evidence_only` keeps the index snapshot and raw
    news for the appendix; the narrative then lives in the opening macro section."""
    if not macro_result:
        return ""

    score = macro_result.get("score", 50)
    rec = macro_result.get("recommendation", "Neutral")
    quant = macro_result.get("quant_score")
    claude = macro_result.get("claude_score")
    analysis = macro_result.get("analysis", {})
    market_data = macro_result.get("market_data", {})
    news = macro_result.get("news", [])

    # Score emoji based on recommendation
    if score >= 75:
        indicator = "STRONG BUY DAY"
    elif score >= 60:
        indicator = "BUY DAY"
    elif score >= 45:
        indicator = "NEUTRAL"
    elif score >= 30:
        indicator = "CAUTIOUS"
    else:
        indicator = "RISK OFF"

    section = f"## Daily Macro Assessment — {indicator}\n\n"
    section += f"**Macro Score: {score:.0f}/100** | **Action: {rec}**"
    if quant is not None:
        section += f" | Quant: {quant:.0f}"
    if claude is not None:
        section += f" | AI: {claude:.0f}"
    section += "\n\n"

    if evidence_only:
        analysis = {}
    # Claude's summary
    if analysis.get("summary"):
        section += f"{analysis['summary']}\n\n"

    # Action guidance
    if analysis.get("action_guidance"):
        section += f"**Today's Guidance:** {analysis['action_guidance']}\n\n"

    # Market data table
    section += "### Market Snapshot\n\n"
    section += "| Index | Price | 1D | 1M | 3M | RSI |\n"
    section += "|-------|-------|-----|-----|-----|-----|\n"
    for ticker in [
        "SPY", "QQQ", "DIA", "IWM", "SMH", "XLE",
        "^VIX", "TLT", "^IRX", "^FVX", "^TNX", "^TYX",
        "GLD", "UUP", "USO", "HYG",
    ]:
        info = market_data.get(ticker, {})
        if not info:
            continue
        name = info.get("name", ticker)
        price = info.get("price", 0)
        chg_1d = info.get("change_1d", 0)
        chg_1m = info.get("change_1m", 0)
        chg_3m = info.get("change_3m", 0)
        rsi = info.get("rsi", "—")
        rsi_str = f"{rsi:.0f}" if isinstance(rsi, (int, float)) else rsi
        if ticker in ("^TNX", "^TYX", "^IRX", "^FVX", "^VIX"):
            price_str = f"{price:.2f}"
        else:
            price_str = f"${price:.2f}"
        section += f"| {name} | {price_str} | {chg_1d:+.2f}% | {chg_1m:+.2f}% | {chg_3m:+.2f}% | {rsi_str} |\n"

    # Bull/Bear case
    if analysis.get("bull_case") or analysis.get("bear_case"):
        section += "\n"
        if analysis.get("bull_case"):
            section += f"**Bull Case:** {analysis['bull_case']}\n\n"
        if analysis.get("bear_case"):
            section += f"**Bear Case:** {analysis['bear_case']}\n\n"

    # Key catalysts and risks
    catalysts = analysis.get("key_catalysts", [])
    risks = analysis.get("key_risks", [])
    if catalysts:
        section += "**Positive Catalysts:**\n"
        for c in catalysts:
            section += f"- {c}\n"
        section += "\n"
    if risks:
        section += "**Key Risks:**\n"
        for r in risks:
            section += f"- {r}\n"
        section += "\n"

    # Cited news
    cited = analysis.get("cited_news", [])
    if cited:
        section += "**Key Market News Driving Today's Assessment:**\n"
        for headline in cited:
            section += f"- {headline}\n"
        section += "\n"

    # All recent market news
    if news:
        section += "### Recent Market News\n\n"
        for article in news[:12]:
            title = article.get("title", "")
            publisher = article.get("publisher", "")
            link = article.get("link", "")
            pub_time = article.get("publish_time")
            age_str = ""
            if pub_time:
                age_days = max(0, (datetime.now() - pub_time).total_seconds() / 86400)
                if age_days < 1:
                    age_str = "today"
                elif age_days < 2:
                    age_str = "1d ago"
                else:
                    age_str = f"{age_days:.0f}d ago"
            pub_str = f" — {publisher}" if publisher else ""
            age_tag = f" [{age_str}]" if age_str else ""
            if link:
                section += f"- [{title}]({link}){pub_str}{age_tag}\n"
            else:
                section += f"- {title}{pub_str}{age_tag}\n"

    section += "\n---\n"
    return section


def _build_bottom_20(ranked):
    """Build Bottom 20 Sell/Avoid section."""
    bottom = ranked[-20:] if len(ranked) >= 20 else ranked
    bottom = list(reversed(bottom))  # Worst first

    section = "\n---\n\n## 评分后列 — 相对排名\n\n"
    section += "股票池中的相对后列，可能仍含 Buy/Hold；减仓与回避以 Recommendation 列为准。\n\n"
    section += "| Rank | Ticker | Name | Score | Grade | 距高点 | 回撤 | 加仓判断 | Recommendation | 主要因素 |\n"
    section += "|------|--------|------|-------|-------|--------|------|----------|----------------|-----------|\n"

    add_map = _add_card_map(bottom)
    for i, r in enumerate(bottom, 1):
        sr = r["score_result"]
        name = r.get("name", r["ticker"])[:20]
        reasons = sr.get("reasoning", [])
        issue = reasons[0] if reasons else "暂无原因摘要"
        if len(issue) > 80:
            issue = issue[:79] + "…"
        card = _row_add(r, add_map)
        section += (
            f"| {i} | **{r['ticker']}** | {name} "
            f"| {sr['score']:.1f} | {sr['grade']} "
            f"| {_fmt_pct(card.get('from_high'))} "
            f"| {card.get('drawdown_label') or '—'} "
            f"| {card.get('add_verdict') or '—'} "
            f"| {sr['recommendation']} "
            f"| {issue} |\n"
        )

    # Score breakdown for bottom 20
    section += "\n### Score Breakdown (Bottom 20)\n\n"
    section += "| Ticker | Technical | Trend | Alpha | Sentiment | Final |\n"
    section += "|--------|-----------|-------|-------|-----------|-------|\n"

    for r in bottom:
        sr = r["score_result"]
        c = sr["components"]
        section += (
            f"| **{r['ticker']}** "
            f"| {c['technical']['score']:.0f} "
            f"| {c['trend']['score']:.0f} "
            f"| {c['alpha']['score']:.0f} "
            f"| {c['sentiment']['score']:.0f} "
            f"| **{sr['score']:.1f}** |\n"
        )

    return section


def _build_portfolio_weights(ranked, macro_result=None):
    """
    Build suggested portfolio weights using concentrated top-N strategy.

    Strategy V7 (weight math lives in strategy.py, shared with live trading):
    - Select top 10 stocks by momentum (3m+1m composite)
    - Weight blend: 95% momentum ranking + 5% score^2
    - Macro overlay: cash reserve 0-20% based on macro conditions
    - Stocks outside top 10 get 0% allocation
    - Max position cap: 25%
    """
    import strategy

    if not ranked or len(ranked) < 2:
        return ""

    top_n = strategy.TOP_N
    max_pos = strategy.MAX_POS

    macro_score = macro_result.get("score", 50) if macro_result else 50
    macro_rec = macro_result.get("recommendation", "Neutral") if macro_result else "Neutral"
    weights_pct, cash_pct, vol_info, mom_data = _target_weights(ranked, macro_result)
    cash_label = f"{cash_pct*100:.1f}% (includes capital left unallocated by position caps"
    if vol_info.get("realized_vol") is not None and vol_info.get("vol_target"):
        cash_label += (f"; vol targeting: basket {vol_info['realized_vol']*100:.0f}% vs "
                       f"target {vol_info['vol_target']*100:.0f}% -> exposure "
                       f"{vol_info['exposure']*100:.0f}%")
    cash_label += ")"
    # Expand to all tickers (0 for excluded) for the display tables below.
    final_weights = {r["ticker"]: weights_pct.get(r["ticker"], 0.0) for r in ranked}

    # Top N by momentum, for the "Mom Rank" display column
    top_by_mom = sorted([r["ticker"] for r in ranked],
                        key=lambda x: mom_data[x], reverse=True)[:top_n]

    # Sort by weight descending
    sorted_stocks = sorted(final_weights.keys(), key=lambda x: final_weights[x], reverse=True)

    # Build section
    section = "\n---\n\n## Suggested Portfolio Allocation\n\n"
    section += f"**Macro Score: {macro_score:.0f}/100 ({macro_rec})** | "
    section += f"**Cash Reserve: {cash_label}**\n\n"
    section += f"**Strategy V7:** Concentrated Top-{top_n} by Momentum | "
    section += "95% momentum + 5% score | "
    section += f"Max position: {max_pos:.0f}% | Vol target: {strategy.VOL_TARGET*100:.0f}%\n\n"
    mom_desc = ("6-month return, latest month skipped" if strategy.MOM_MODE == "6_1"
                else "3-month + 1-month composite")
    section += f"Top 10 stocks selected by **momentum rank** ({mom_desc}). "
    section += "Capital weighted toward the strongest momentum names with score as a minor quality tilt. "
    section += "Stocks outside top 10 are excluded (0% weight).\n\n"
    section += "This allocation assumes a fresh portfolio; live trading also applies its existing-holding rank buffer.\n\n"

    # Top holdings table (the ones that get allocation)
    allocated = [t for t in sorted_stocks if final_weights[t] > 0.1]
    not_allocated = [t for t in sorted_stocks if final_weights[t] <= 0.1]

    section += f"### Portfolio Holdings ({len(allocated)} stocks)\n\n"
    section += "| Rank | Ticker | Weight | 6M-1 | Mom Rank |\n"
    section += "|------|--------|--------|------|----------|\n"

    for i, t in enumerate(allocated, 1):
        r = next(x for x in ranked if x["ticker"] == t)
        sr = r["score_result"]
        ind = r.get("indicators", {})
        name = r.get("name", t)[:18]
        w = final_weights[t]
        m1 = ind.get("change_1m", 0)
        m3 = ind.get("change_3m", 0)
        m6s = ind.get("change_6m_skip1")
        mom_rank = top_by_mom.index(t) + 1 if t in top_by_mom else "-"

        section += (
            f"| {i} | **{t}** "
            f"| {w:.1f}% "
            f"| {_fmt_pct(m6s)} "
            f"| #{mom_rank} |\n"
        )

    # Show total allocated
    total_allocated = sum(final_weights[t] for t in allocated)
    section += f"\n**Total Invested:** {total_allocated:.1f}% | "
    section += f"**Cash:** {cash_pct*100:.0f}%\n"

    if not_allocated:
        section += f"\n另有 {len(not_allocated)} 只未获配置（0%）；评分与涨跌见「个股总表」。\n"

    section += "\n"
    return section


def _target_weights(ranked, macro_result=None):
    """Strategy target weights (same math as live trading): (weights %, cash fraction, vol info, momentum)."""
    import strategy
    macro_score = macro_result.get("score", 50) if macro_result else 50
    mom_data, returns_by_ticker = {}, {}
    for r in ranked:
        ind = r.get("indicators", {}) or {}
        mom_data[r["ticker"]] = strategy.momentum_signal(ind)
        if ind.get("daily_returns"):
            returns_by_ticker[r["ticker"]] = ind["daily_returns"]
    stocks = [{"ticker": r["ticker"], "score": r["score_result"]["score"],
               "mom": mom_data[r["ticker"]]} for r in ranked]
    weights_pct, cash_pct, vol_info = strategy.compute_target_weights_vol_managed(
        stocks, macro_score=macro_score, returns_by_ticker=returns_by_ticker)
    return weights_pct, cash_pct, vol_info, mom_data


def _build_executive_summary(ranked, counts, macro_result, verdict, cards, option_event,
                             decisions, books, swing):
    """Conclusions only, in reading order: macro verdict first, then what to do."""
    import macro_brief
    from report_sections import unique
    def names(items, limit=8):
        items = list(items)
        return "、".join(items[:limit]) + (f" 等 {len(items)} 只" if len(items) > limit else "")
    lines = ["## Executive Summary — 今日结论\n"]
    if verdict:
        lines.append(f"- **宏观：** {macro_brief.one_line(verdict)}。")
        reserve = verdict.get("cash_reserve_pct")
        lines.append(f"- **个股节奏：** {verdict.get('stock_line', '—')}。**期权节奏：** {verdict.get('options_line', '—')}。")
        if verdict.get("divergence"):
            lines.append("- **⚠️ 信号分歧：** 总仓位栏偏防守，而现金阶梯或宏观带已给出分批买入条件。两者管的不是同一笔钱："
                         "前者约束已有的高 beta 动量仓和追涨，后者是单独现金池的分批；各按各的规则执行，不互相抵消也不叠加。")
    lines.append("- **评级分布：** " + " · ".join(f"{k} {v}" for k, v in counts.items()) + f"（共 {len(ranked)} 只）。")
    try:
        import daily_watch
        dips = [c["ticker"] for c in daily_watch.dip_candidates(cards)]
        adds = [c["ticker"] for c in daily_watch.add_candidates(cards)]
        waiting = [c["ticker"] for c in daily_watch.buy_picks(cards, include_watch=True, n=20)
                   if c["buy_action"] == "watch_buy"][:5]
        bits = [f"{label} {names(group)}" for label, group in (("抄底", dips), ("加仓", adds), ("候补等回踩", waiting)) if group]
        lines.append("- **今日可买：** " + ("；".join(bits) if bits else "没有过线的抄底或加仓候选") + "。")
        avoid = [f"{label} {names(group)}" for label, group in (
            ("急涨不追", [c["ticker"] for c in cards if c.get("stock_action") == "no_chase"]),
            ("过度延伸", [c["ticker"] for c in cards if c.get("stock_action") == "trim_extended"]),
            ("弱势急跌不接", [c["ticker"] for c in cards if c.get("stock_action") == "avoid_knife"])) if group]
        if avoid:
            lines.append("- **不追 / 不接：** " + "；".join(avoid) + "。")
    except Exception:
        pass
    if swing:
        try:
            import leveraged_swing
            head = leveraged_swing.headline(swing)
            if head:
                lines.append(f"- **波段（3x ETF）：** {head}。")
        except Exception:
            pass
    candidates = [t for t, d in (decisions or {}).items() if (d or {}).get("candidates")]
    stance = (option_event or {}).get("label") or "—"
    lines.append(f"- **期权：** 立场「{stance}」；" +
                 (f"通过统一评分的候选 {names(candidates)}" if candidates else "本次没有通过统一评分的合约候选") + "。")
    hits = []
    for label, data in books or []:
        s = (data or {}).get("summary") or {}
        got = unique((s.get("in_zone") or []) + (s.get("open_now") or []) + (s.get("add_now") or []))
        if got:
            hits.append(f"{label} {names(got, 6)}")
    if hits:
        lines.append("- **主题价位触发：** " + "；".join(hits) + "（仍需入场确认）。")
    plan = (macro_result or {}).get("cash_entry_plan") or {}
    if plan.get("tier"):
        target = plan.get("target_pct")
        lines.append(f"- **现金池：** 档位「{plan['tier']}」" +
                     (f"，累计投入目标 {target:g}%" if isinstance(target, (int, float)) else "") + "（细则见下一栏）。")
    try:
        weights, cash_pct, _vol, _mom = _target_weights(ranked, macro_result)
        top = sorted(weights.items(), key=lambda kv: -kv[1])[:3]
        if top:
            lines.append("- **动量组合：** 前三大目标权重 " + "、".join(f"{t} {w:.1f}%" for t, w in top) +
                         f"；现金 {cash_pct * 100:.0f}%" +
                         (f"（其中宏观预留 {verdict['cash_reserve_pct']:.0f}%）" if verdict and verdict.get("cash_reserve_pct") is not None else "") + "。")
    except Exception:
        pass
    if verdict and (verdict.get("triggered") or verdict.get("near")):
        lines.append("- **需要盯的减仓/对冲条件：** " +
                     "；".join([f"🔴 {x}" for x in verdict["triggered"]] + [f"🟡 {x}" for x in verdict["near"]]) + "。")
    lines.append("\n评分评级、当日入场条件和期权适配分口径不同，都不是胜率。\n")
    return "\n".join(lines)


def _put_plan_summary(plan):
    """Watch-list sell-put answers in one table; quotes and levels only, no repeated stock stats."""
    import ai_sell_put_plan as ap
    from report_sections import table
    names = (plan or {}).get("names") or []
    if not names:
        return ""
    rows = [[f"**{p['ticker']}**", ap._money(p.get("spot")), p.get("entry_label") or "—",
             p.get("today_label") or "—", ap._levels_txt(p), ap._pick_txt(p)] for p in names]
    return ("### 名单 Sell Put 方案（逐只答案）\n\n"
            "固定名单每天给出：能否用 sell put 建仓、今天加不加、买点和首选合约。它是名单式参考，"
            "不代替上方的统一评分；股票和 put 二选一。规则与逐只明细见期权附录。\n\n" +
            table(["代码", "现价", "建仓", "今日加仓", "买点(回踩/分批/接股)", "Sell Put 首选"], rows))


def _build_stock_table(ranked, macro_result=None, books=None):
    """Every scored name once: price action, score, today's call, target weight."""
    if not ranked:
        return ""
    from report_sections import table, theme_entries
    add_map = _add_card_map(ranked, macro_result)
    calls = {"no_chase": "急涨，不追", "trim_extended": "过度延伸，不加", "avoid_knife": "弱势急跌，不接"}
    tags = {}
    try:
        for entry in theme_entries(books or []):
            tags[entry["ticker"]] = " / ".join(entry["tags"])
    except Exception:
        pass
    try:
        from configs import UNIVERSE
    except Exception:
        UNIVERSE = {}
    rows = []
    for i, r in enumerate(ranked, 1):
        sr, ind = r["score_result"], r.get("indicators") or {}
        card = _row_add(r, add_map)
        ticker = r["ticker"]
        rows.append([i, f"**{ticker}**", tags.get(ticker) or (UNIVERSE.get(ticker) or {}).get("sector") or "—",
                     _fmt_money(ind.get("price")), _fmt_pct(ind.get("change_1d")),
                     _fmt_pct(ind.get("change_1m")), _fmt_pct(ind.get("change_3m")),
                     _fmt_pct(ind.get("pct_from_52w_high", card.get("from_high"))), f"{sr['score']:.1f}", sr["recommendation"],
                     calls.get(card.get("stock_action")) or card.get("add_verdict") or "—"])
    section = ("## 个股总表 — 评分、涨跌与今日判断\n\n"
               "股票池每只一行，按评分排序。评分是池内相对比较，后列不等于应卖出；能不能买看「今日判断」。"
               "固定观点与价位见主题观察，动量组合权重见 Suggested Portfolio，逐股依据见 Detailed Analysis。\n\n" +
               table(["#", "股票", "主题 / 板块", "现价", "1日", "1月", "3月", "距高点", "评分", "评级", "今日判断"], rows))
    sector = _sector_overview(ranked)
    return section + ("\n" + sector if sector else "")


def _sector_overview(ranked):
    """Sub-sector aggregates only; individual names are already in the stock table."""
    try:
        from configs import UNIVERSE
    except Exception:
        return ""
    from report_sections import table
    groups = [("半导体 / 芯片", {"Semiconductors"}), ("存储 / 内存", {"AI Data Storage"}),
              ("AI 基础设施 / 网络", {"AI Infrastructure", "AI Cloud", "Networking", "Photonics"}),
              ("大科技平台", {"Technology", "Consumer Cyclical", "Communication", "EV & Clean Energy", "Social Media"}),
              ("软件 / 安全 / 数据", {"Enterprise Software", "Cybersecurity", "Data & Analytics", "AI Software"}),
              ("电力 / 能源", {"Utilities", "Energy / Power Equipment", "Energy"})]
    def avg(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    rows = []
    for label, sectors in groups:
        members = [r for r in ranked if (UNIVERSE.get(r["ticker"]) or {}).get("sector") in sectors]
        if not members:
            continue
        ind = lambda r, k: (r.get("indicators") or {}).get(k)
        buys = sum(r["score_result"]["recommendation"] in ("Strong Buy", "Buy") for r in members)
        best = max(members, key=lambda r: r["score_result"]["score"])
        rows.append([label, len(members), f"{avg([r['score_result']['score'] for r in members]):.0f}",
                     _fmt_pct(avg([ind(r, 'change_1m') for r in members])),
                     _fmt_pct(avg([ind(r, 'change_3m') for r in members])),
                     _fmt_pct(avg([ind(r, 'pct_from_52w_high') for r in members])), buys,
                     f"{best['ticker']}（{best['score_result']['score']:.0f}）"])
    if len(rows) < 2:
        return ""
    return ("### 板块强弱\n\n" +
            table(["板块", "只数", "平均分", "平均 1月", "平均 3月", "平均距高点", "Buy 数", "评分最高"], rows))


def _build_rank_highlights(ranked, macro_result=None, n=10):
    """Top and bottom of the score ranking with the main reason, in the main body."""
    if len(ranked) < 4:
        return ""
    n = min(n, len(ranked) // 2)
    add_map = _add_card_map(ranked, macro_result)
    from report_sections import table
    def rows(group, start):
        out = []
        for i, r in enumerate(group, start):
            sr, ind = r["score_result"], r.get("indicators") or {}
            card = _row_add(r, add_map)
            reason = " ".join(str((sr.get("reasoning") or ["—"])[0]).split())
            out.append([i, f"**{r['ticker']}**", f"{sr['score']:.1f}", sr["recommendation"],
                        _fmt_pct(ind.get("change_1m")), _fmt_pct(ind.get("change_3m")),
                        _fmt_pct(card.get("from_high")), card.get("add_verdict") or "—",
                        reason if len(reason) <= 90 else reason[:89] + "…"])
        return out
    headers = ["排名", "股票", "评分", "评级", "1月", "3月", "距高点", "今日判断", "主要因素"]
    return ("## 评分前列与后列 — 相对排名\n\n"
            "评分是股票池内的相对比较：前列不等于今天就买（看「今日判断」），后列也不等于应卖出。完整排名见评分附录。\n\n"
            f"### 评分前 {n}\n\n" + table(headers, rows(ranked[:n], 1)) +
            f"\n### 评分后 {n}\n\n" + table(headers, rows(ranked[-n:], len(ranked) - n + 1)))


def _build_alpaca_performance():
    """Build Alpaca paper/live portfolio performance section."""
    import os

    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not api_secret:
        return ""

    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(api_key, api_secret, paper=True)
        account = client.get_account()
        positions = client.get_all_positions()

        equity = float(account.equity)
        cash = float(account.cash)
        buying_power = float(account.buying_power)

        # Calculate total P&L (assuming $100k starting capital)
        starting_capital = 100000.0
        total_pnl = equity - starting_capital
        total_return_pct = (equity / starting_capital - 1) * 100

        section = "## Portfolio Performance (Alpaca Paper)\n\n"
        section += f"**Equity:** ${equity:,.2f} | "
        section += f"**Cash:** ${cash:,.2f} | "
        section += f"**Invested:** ${equity - cash:,.2f}\n\n"
        section += f"**Total P&L:** ${total_pnl:+,.2f} ({total_return_pct:+.2f}%) | "
        section += f"**Starting Capital:** ${starting_capital:,.0f}\n\n"

        if not positions:
            section += "*No open positions*\n\n---\n"
            return section

        # Sort positions by market value descending
        positions.sort(key=lambda p: float(p.market_value), reverse=True)

        section += f"### Open Positions ({len(positions)})\n\n"
        section += "| Ticker | Qty | Avg Cost | Price | Mkt Value | P&L | P&L % | Weight |\n"
        section += "|--------|-----|----------|-------|-----------|-----|-------|--------|\n"

        total_market_value = sum(float(p.market_value) for p in positions)

        for p in positions:
            ticker = p.symbol
            qty = float(p.qty)
            avg_cost = float(p.avg_entry_price)
            current_price = float(p.current_price)
            market_value = float(p.market_value)
            unrealized_pl = float(p.unrealized_pl)
            unrealized_plpc = float(p.unrealized_plpc) * 100
            weight = (market_value / equity * 100) if equity > 0 else 0

            section += (
                f"| **{ticker}** "
                f"| {qty:.0f} "
                f"| ${avg_cost:.2f} "
                f"| ${current_price:.2f} "
                f"| ${market_value:,.0f} "
                f"| ${unrealized_pl:+,.0f} "
                f"| {unrealized_plpc:+.1f}% "
                f"| {weight:.1f}% |\n"
            )

        # Summary
        total_unrealized = sum(float(p.unrealized_pl) for p in positions)
        section += f"\n**Total Unrealized P&L:** ${total_unrealized:+,.2f} | "
        section += f"**Positions:** {len(positions)} | "
        section += f"**Cash Weight:** {cash / equity * 100:.1f}%\n"

        section += "\n---\n"
        return section

    except Exception as e:
        return f"## Portfolio Performance (Alpaca Paper)\n\n*Unable to fetch: {e}*\n\n---\n"


def _build_ai_semi_section(ranked):
    """Build the AI / Semiconductor / Chip / Storage thematic analysis (in Chinese).

    Groups stocks by their *config* sector (configs.UNIVERSE), not the coarse
    yfinance sector, so the AI-hardware taxonomy (Semiconductors, AI
    Infrastructure, Memory & Storage, etc.) is reliable.
    """
    if not ranked:
        return ""

    try:
        from configs import UNIVERSE
    except Exception:
        UNIVERSE = {}

    # Theme groups → set of config sectors, ordered hardware-first.
    GROUPS = [
        ("半导体 / 芯片", {"Semiconductors"}),
        ("存储 / 内存", {"AI Data Storage"}),
        ("AI 基础设施 / 数据中心", {"AI Infrastructure", "AI Cloud"}),
        ("AI 网络 / 光通信", {"Networking", "Photonics"}),
        ("AI 软件 / 数据", {"AI Software", "Data & Analytics", "AI Healthcare", "AI Cybersecurity"}),
    ]

    def cfg_sector(ticker):
        meta = UNIVERSE.get(ticker, {})
        return meta.get("sector", "")

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    # Bucket the in-scope stocks by group.
    in_scope_sectors = set().union(*[s for _, s in GROUPS])
    scoped = [r for r in ranked if cfg_sector(r["ticker"]) in in_scope_sectors]
    if not scoped:
        return ""

    section = "## AI / 半导体 / 芯片 / 存储 板块分析\n\n"

    # --- Complex-wide summary ---
    all_scores = [r["score_result"]["score"] for r in scoped]
    all_1m = [r.get("indicators", {}).get("change_1m") for r in scoped]
    all_3m = [r.get("indicators", {}).get("change_3m") for r in scoped]
    n_buy = sum(1 for r in scoped
                if r["score_result"]["recommendation"] in ("Strong Buy", "Buy"))
    avg_score = avg(all_scores) or 0
    avg_1m = avg(all_1m)
    avg_3m = avg(all_3m)

    # 板块整体定调
    if avg_score >= 65:
        mood = "强势（资金明显偏好该主题）"
    elif avg_score >= 55:
        mood = "偏强（多数标的处于上升趋势）"
    elif avg_score >= 45:
        mood = "中性（分化，需精选个股）"
    elif avg_score >= 35:
        mood = "偏弱（趋势走弱，注意减仓）"
    else:
        mood = "弱势（主题性回避）"

    section += (
        f"**主题整体：{mood}** —— 覆盖 {len(scoped)} 只标的，"
        f"平均评分 **{avg_score:.0f}/100**，"
        f"其中 Buy/Strong Buy 共 {n_buy} 只（占 {n_buy/len(scoped)*100:.0f}%）。"
    )
    if avg_1m is not None and avg_3m is not None:
        section += f" 平均涨幅：近 1 月 {avg_1m:+.1f}%、近 3 月 {avg_3m:+.1f}%。"
    section += "\n\n"

    # --- Per-group breakdown table ---
    section += "### 子板块概览\n\n"
    section += "| 子板块 | 标的数 | 平均分 | 平均 1M | 平均 3M | Buy 数 | 领涨标的 |\n"
    section += "|--------|-------|-------|---------|---------|-------|----------|\n"

    group_buckets = []
    for label, sectors in GROUPS:
        members = [r for r in scoped if cfg_sector(r["ticker"]) in sectors]
        if not members:
            continue
        group_buckets.append((label, members))
        g_score = avg([r["score_result"]["score"] for r in members]) or 0
        g_1m = avg([r.get("indicators", {}).get("change_1m") for r in members])
        g_3m = avg([r.get("indicators", {}).get("change_3m") for r in members])
        g_buy = sum(1 for r in members
                    if r["score_result"]["recommendation"] in ("Strong Buy", "Buy"))
        # 组内按 3 月动量排序取领涨
        leader = max(
            members,
            key=lambda r: (r.get("indicators", {}).get("change_3m") or -1e9),
        )
        leader_3m = leader.get("indicators", {}).get("change_3m")
        leader_str = f"{leader['ticker']}"
        if leader_3m is not None:
            leader_str += f" ({leader_3m:+.0f}%)"
        g_1m_str = f"{g_1m:+.1f}%" if g_1m is not None else "—"
        g_3m_str = f"{g_3m:+.1f}%" if g_3m is not None else "—"
        section += (
            f"| {label} | {len(members)} | {g_score:.0f} "
            f"| {g_1m_str} | {g_3m_str} | {g_buy} | {leader_str} |\n"
        )

    # --- Leaders / laggards across the whole complex (by 3M momentum) ---
    def mom3(r):
        return r.get("indicators", {}).get("change_3m")

    with_mom = [r for r in scoped if mom3(r) is not None]
    if with_mom:
        by_mom = sorted(with_mom, key=mom3, reverse=True)
        leaders = by_mom[:5]
        laggards = list(reversed(by_mom[-5:]))

        section += "\n### 主题内动量排行（近 3 月）\n\n"
        section += "**领涨 Top 5：**\n\n"
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 距高点 | 回撤 | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|--------|------|------|\n"
        for r in leaders:
            section += _ai_semi_row(r, cfg_sector(r["ticker"]))
        section += "\n**落后 Bottom 5：**\n\n"
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 距高点 | 回撤 | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|--------|------|------|\n"
        for r in laggards:
            section += _ai_semi_row(r, cfg_sector(r["ticker"]))

    # --- Short Chinese takeaway ---
    section += "\n### 板块解读\n\n"
    strongest = max(group_buckets, key=lambda g: avg([x["score_result"]["score"] for x in g[1]]) or 0) if group_buckets else None
    weakest = min(group_buckets, key=lambda g: avg([x["score_result"]["score"] for x in g[1]]) or 0) if group_buckets else None
    if strongest and weakest and strongest[0] != weakest[0]:
        s_avg = avg([x["score_result"]["score"] for x in strongest[1]]) or 0
        w_avg = avg([x["score_result"]["score"] for x in weakest[1]]) or 0
        section += (
            f"- **最强子板块：{strongest[0]}**（平均分 {s_avg:.0f}），"
            f"**最弱子板块：{weakest[0]}**（平均分 {w_avg:.0f}）。\n"
        )
    if avg_3m is not None:
        if avg_3m > 10:
            section += "- 该主题近 3 月显著跑赢，动量策略会重仓此处，但需警惕高位回撤与估值透支。\n"
        elif avg_3m < -5:
            section += "- 该主题近 3 月走弱，趋势/动量信号转差，仓位上应保持谨慎、等待企稳确认。\n"
        else:
            section += "- 该主题近 3 月震荡分化，建议聚焦评分与动量同时居前的龙头，回避落后标的。\n"
    section += (
        "- 子板块判定依据 `configs.py` 的细分行业字段；"
        "评分综合技术、趋势、Alpha 与情绪四维度。仅供参考，非投资建议。\n"
    )

    section += "\n---\n"
    return section


def _ai_semi_row(r, sub_sector):
    """Render one Chinese-section table row for the AI/semi momentum tables."""
    sr = r["score_result"]
    ind = r.get("indicators", {})
    name = r.get("name", r["ticker"])[:16]
    m1 = ind.get("change_1m")
    m3 = ind.get("change_3m")
    m1_str = f"{m1:+.1f}%" if m1 is not None else "—"
    m3_str = f"{m3:+.1f}%" if m3 is not None else "—"
    rec_map = {
        "Strong Buy": "强烈买入", "Buy": "买入", "Hold": "持有",
        "Reduce": "减仓", "Avoid": "回避",
    }
    rec = rec_map.get(sr["recommendation"], sr["recommendation"])
    fh = ind.get("pct_from_52w_high", ind.get("from_high"))
    try:
        import daily_watch
        dd = daily_watch.drawdown_label(fh)
    except Exception:
        dd = "—"
    return (
        f"| **{r['ticker']}** | {name} | {sub_sector} "
        f"| {sr['score']:.1f} | {m1_str} | {m3_str} "
        f"| {_fmt_pct(fh)} | {dd} | {rec} |\n"
    )


def _add_card_map(ranked, macro_result=None):
    try:
        import daily_watch
        cards, _ = daily_watch.annotate(
            ranked, tape=(macro_result or {}).get("macro_tape"))
        return {c["ticker"]: c for c in cards}
    except Exception:
        return {}


def _row_add(row, add_map=None):
    ticker = (row.get("ticker") or "").upper()
    card = (add_map or {}).get(ticker)
    if card:
        return card
    ind = row.get("indicators") or {}
    fh = ind.get("pct_from_52w_high", ind.get("from_high"))
    try:
        import daily_watch
        dd = daily_watch.drawdown_label(fh)
    except Exception:
        dd = "—"
    return {
        "from_high": fh,
        "drawdown_label": dd,
        "add_verdict": "—",
        "from_sma50": ind.get("pct_from_sma50"),
        "from_sma200": ind.get("pct_from_sma200"),
    }


def _fmt_money(val):
    if val is None:
        return "—"
    try:
        return f"${float(val):.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_num(val, digits=0):
    if val is None:
        return "—"
    try:
        return f"{float(val):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(val, digits=1):
    if val is None:
        return "—"
    try:
        return f"{float(val):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _build_stock_trend_section(ranked, macro_result=None):
    """Cash entry plan. The index trend and outlook live in the macro section."""
    import cash_entry_plan
    market_data = (macro_result or {}).get("market_data") or {}
    cash_plan = (macro_result or {}).get('cash_entry_plan') or cash_entry_plan.from_market_data(market_data)
    body = cash_entry_plan.render(cash_plan, compact=True)
    return "## General — 指数现金入场计划\n\n" + body.replace("### 现金入场计划 — VIX × SPX 分批入场\n", "", 1).lstrip("\n")


def _build_market_overview(ranked):
    """Back-compat wrapper. The daily report uses `_build_stock_trend_section`."""
    return _build_stock_trend_section(ranked, None)


def _ticker_list(items, max_show=10):
    """Format ticker list for summary table."""
    tickers = [r["ticker"] for r in items[:max_show]]
    result = ", ".join(tickers)
    if len(items) > max_show:
        result += f", +{len(items) - max_show} more"
    return result if result else "-"


def _compact_stock_news(row):
    """Keep research input intact; cap only the report presentation."""
    detail = row.get("score_result", {}).get("sentiment_detail") or {}
    def short(value, limit):
        value = " ".join(str(value).split())
        return value if len(value) <= limit else value[:limit-1] + "…"
    lines = []
    themes = detail.get("key_themes") or []
    summary = "；".join(str(t) for t in themes[:2]) or detail.get("reasoning")
    if summary:
        lines.append("**News Brief:** " + short(summary, 180))
    flags = detail.get("risk_flags") or []
    if flags:
        lines.append("**News Risks:** " + short("；".join(str(f) for f in flags[:2]), 180))
    seen_titles, seen_links = set(), set()
    sources = []
    for article in row.get("news_data") or []:
        title = " ".join(str(article.get("title") or "").split())
        link = article.get("link") or ""
        key = title.casefold()
        if not title or key in seen_titles or (link and link in seen_links):
            continue
        seen_titles.add(key)
        seen_links.add(link)
        title = short(title, 120)
        sources.append(f"- [{title}]({link})" if link else f"- {title}")
        if len(sources) == 2:
            break
    if sources:
        lines.append("**News Sources:**\n" + "\n".join(sources))
    return "\n" + "\n".join(lines) + "\n" if lines else ""


def _build_score_research(ranked):
    rows = [r for r in ranked if r['score_result'].get('research_signals', {}).get('signals')]
    if not rows:
        return ""
    labels = {'residual_strength':'市场调整强度', 'downside_resilience':'抗跌表现',
              'close_location_volume':'量价收盘位置'}
    def fmt(x):
        return '—' if x is None else f'{x:.1f}'
    lines = ['## 个股评分差异 — 绝对评分与同池比较\n',
             '绝对评分决定评级；同池百分位仅表示本次股票池内的相对位置（至少10只），不是胜率。',
             '候选模型最多调整 ±12 分；历史验证未显示稳定排序改善，因此候选分不改变实际评级或下单。缺失项不加分。\n',
             '| 股票 | 实际分 | 候选分（未启用） | 候选调整 | 同池百分位 | 市场调整强度 | 抗跌表现 | 量价收盘位置 |',
             '|---|---|---|---|---|---|---|---|']
    for row in rows[:20]:
        sr = row['score_result']
        signals = sr['research_signals']['signals']
        vals = ' | '.join(fmt(signals.get(k, {}).get('score')) for k in labels)
        lines.append(f"| {row['ticker']} | {sr['score']:.1f} | {fmt(sr.get('research_score'))} | "
                     f"{sr.get('research_adjustment',0):+.1f} | {fmt(sr.get('peer_percentile'))} | {vals} |")
    lines += ['', '信号列也是0–100分：市场调整强度剔除估计 beta 的影响；抗跌表现观察市场下跌日；量价收盘位置不是实际资金流入。',
              '同池百分位随股票池改变，不能跨池直接比较；弱市的第一名也可能仍应回避。\n', '---\n']
    return '\n'.join(lines)
