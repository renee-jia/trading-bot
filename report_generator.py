"""
Report generator for stock recommendations.

Generates clean Markdown reports with:
- General stock-trend outlook (1 week / 1 month / 6 months) and add-vs-sell stance
- Macro desk (rates, dollar, vol, risk appetite, event calendar)
- Options desk (today's premium stance + tickets)
- Config watch + top movers (chase vs dip)
- Executive summary
- Top picks ranked by score
- Detailed per-stock analysis with reasoning
"""
from datetime import datetime
from pathlib import Path


def generate_report(scored_results, output_dir="reports", macro_result=None,
                    discovery_result=None, options_decisions=None,
                    sell_put_plan=None, ai_portfolio=None, saas_watch=None):
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
    report = _build_report(ranked, macro_result=macro_result,
                          discovery_result=discovery_result,
                          options_research_section=options_section,
                          sell_put_plan=sell_put_plan, ai_portfolio=portfolio,
                          saas_watch=saas)

    Path(report_path).write_text(report, encoding="utf-8")
    from report_format import render_html
    Path(report_path).with_suffix(".html").write_text(render_html(report), encoding="utf-8")

    return report_path, report


def _build_report(ranked, macro_result=None, discovery_result=None, options_research_section="",
                  sell_put_plan=None, ai_portfolio=None, saas_watch=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(ranked)
    data_as_of = max((r.get("data_as_of") for r in ranked if r.get("data_as_of")), default="未知")

    # Categorize
    strong_buys = [r for r in ranked if r["score_result"]["recommendation"] == "Strong Buy"]
    buys = [r for r in ranked if r["score_result"]["recommendation"] == "Buy"]
    holds = [r for r in ranked if r["score_result"]["recommendation"] == "Hold"]
    reduces = [r for r in ranked if r["score_result"]["recommendation"] == "Reduce"]
    avoids = [r for r in ranked if r["score_result"]["recommendation"] == "Avoid"]

    score_research = _build_score_research(ranked)

    # General: current trend + 1w/1m/6m outlook + add-vs-sell stance
    trend_section = _build_stock_trend_section(ranked, macro_result)

    # Build macro snapshot (daily score, news, index table)
    macro_section = _build_macro_section(macro_result) if macro_result else ""

    # AI / Semiconductor / Chip / Storage thematic sector analysis
    ai_semi_section = _build_ai_semi_section(ranked)

    # Build Alpaca portfolio performance section
    alpaca_section = _build_alpaca_performance()

    try:
        import daily_watch
        macro_desk = daily_watch.build_macro_desk_section(macro_result)
        buy_section = daily_watch.build_buy_section(
            ranked, macro_result=macro_result
        )
        options_desk = daily_watch.build_options_desk_section(
            ranked, macro_result=macro_result
        )
        config_watch = daily_watch.build_config_watch_section(
            ranked, macro_result=macro_result
        )
        movers_section = daily_watch.build_top_movers_section(
            ranked, macro_result=macro_result
        )
    except Exception as e:
        macro_desk = f"## Macro Desk — 今日宏观\n\n*Section failed: {e}*\n\n---\n"
        buy_section = f"## 今日建议买入\n\n*Section failed: {e}*\n\n---\n"
        options_desk = f"## Options Desk — 今日期权操作\n\n*Section failed: {e}*\n\n---\n"
        config_watch = ""
        movers_section = ""

    decisions = {r['ticker']:r['options_decision'] for r in ranked if r.get('options_decision') is not None}

    # AI Portfolio: the separately watched core AI list (roll-up, no new fetches)
    try:
        import ai_portfolio as ai_portfolio_desk
        if ai_portfolio is None:
            ai_portfolio = ai_portfolio_desk.build(ranked, macro_result=macro_result,
                                                   sell_put_plan=sell_put_plan)
        if ai_portfolio.get("error"):
            raise RuntimeError(ai_portfolio["error"])
        ai_portfolio_section = ai_portfolio_desk.build_section(ai_portfolio)
    except Exception as e:
        ai_portfolio_section = f"## AI Portfolio — 核心 AI 名单\n\n*Section failed: {e}*\n\n---\n"

    # SaaS Watch: fixed software list with the user's star ratings (roll-up, no new fetches)
    try:
        import saas_watch as saas_desk
        if saas_watch is None:
            saas_watch = saas_desk.build(ranked, macro_result=macro_result,
                                         sell_put_plan=sell_put_plan)
        if saas_watch.get("error"):
            raise RuntimeError(saas_watch["error"])
        saas_section = saas_desk.build_section(saas_watch)
    except Exception as e:
        saas_section = f"## SaaS Watch — 软件 SaaS 名单\n\n*Section failed: {e}*\n\n---\n"

    # Covered-call ladders for the CC_WATCH ticker list (empty when unset; no position data):
    # per-holding ladders first, then the unified-score second opinion.
    try:
        import covered_call_advisor
        cc_section = covered_call_advisor.build_covered_call_ladders()
    except Exception as e:
        cc_section = f"## Covered Call Advisor（写 call 到期日 / 行权价参考）\n\n*Section failed: {e}*\n\n---\n"
    try:
        cc_section += "\n" + covered_call_advisor.build_covered_call_section(decisions=decisions)
    except Exception as e:
        cc_section += f"\n## Covered Call — 统一评分候选\n\n*Section failed: {e}*\n\n---\n"

    # Sell-put radar: scan the whole universe for panic-drop premium setups,
    # then the unified-score cash-secured put candidates.
    try:
        import sell_put_advisor
        sp_section = sell_put_advisor.build_sell_put_radar(ranked)
    except Exception as e:
        sp_section = f"## Sell Put 雷达（大跌收租机会）\n\n*Section failed: {e}*\n\n---\n"
    try:
        sp_section += "\n" + sell_put_advisor.build_sell_put_section(ranked, decisions=decisions)
    except Exception as e:
        sp_section += f"\n## Cash-secured Put — 统一评分候选\n\n*Section failed: {e}*\n\n---\n"

    # AI 持有标的 sell-put plan: fixed AI watch list, answered every day
    try:
        import ai_sell_put_plan
        if sell_put_plan is None:
            sell_put_plan = ai_sell_put_plan.build_plan(ranked, macro_result=macro_result)
        if sell_put_plan.get("error"):
            raise RuntimeError(sell_put_plan["error"])
        ai_put_section = ai_sell_put_plan.build_section(sell_put_plan)
    except Exception as e:
        ai_put_section = f"## AI 持有标的 Sell Put 方案\n\n*Section failed: {e}*\n\n---\n"

    report = f"""# Trading Bot · 每日策略报告

**生成时间：** {now}（本机时区） · **数据截至：** {data_as_of}（最近一根完整日线收盘） · **分析股票：** {total} 只

**策略周期：** 数周以上持有；期权另按期限、报价与事件条件筛选。

> 阅读顺序：先看 General 的风险预算与现金入场计划，再看个股入场条件和期权候选。股票评级是综合评分，候选研究分和期权适配分都不是胜率。

---

## Executive Summary — 今日概览

| Category | Count | Tickers |
|----------|-------|---------|
| Strong Buy | {len(strong_buys)} | {_ticker_list(strong_buys)} |
| Buy | {len(buys)} | {_ticker_list(buys)} |
| Hold | {len(holds)} | {_ticker_list(holds)} |
| Reduce | {len(reduces)} | {_ticker_list(reduces)} |
| Avoid | {len(avoids)} | {_ticker_list(avoids)} |

---

{trend_section}

{macro_desk}

{ai_portfolio_section}

{saas_section}

{buy_section}

{score_research}

{options_desk}

{options_research_section}

{config_watch}

{movers_section}

{cc_section}

{sp_section}

{ai_put_section}

{macro_section}

{alpaca_section}

{ai_semi_section}

---

## Scoring Methodology

Each stock is evaluated across four dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Technical | 35% | Trend (MA crossovers, ADX), momentum (RSI, MACD), volume, volatility |
| Trend | 30% | Relative strength vs SPY, market regime, long-term momentum |
| Alpha | 20% | 30 quantitative alpha factors (momentum, mean-reversion, volume) |
| Sentiment | 15% | News headline sentiment, recency-weighted |

**Score Scale:** 0-100 (75+ = Strong Buy, 60-74 = Buy, 45-59 = Hold, 30-44 = Reduce, <30 = Avoid)

---

## Portfolio Strategy

**Strategy V6: Concentrated Top-10 by Momentum.** In `backtest_strategy.py` (1-year window, current large-cap universe, T+1 execution, 5 bps/side costs) the strategy modestly out-returned equal-weight buy-and-hold (low-single-digit % alpha) but at higher volatility and drawdown — so the edge is return, not risk-adjusted, and it is survivorship-biased toward today's winners. Treat backtested alpha as an upper bound.

**How it works:**
1. **Stock Selection:** Select top 10 stocks by **momentum rank** (70% 3-month + 30% 1-month composite return). Momentum is a better filter than score for catching breakouts early.
2. **Weight Allocation:** Within the top 10, blend 95% momentum ranking + 5% score-squared weighting. The highest-momentum stock gets ~3x the weight of the lowest.
3. **Rebalancing:** The bot re-examines the book on every scheduled run (the backtest models a biweekly, 14-trading-day cadence), moving 80% of the way toward target weights each time.
4. **Risk Management:** Max single position capped at 25%. Macro-based cash reserve (0-20%) adjusts exposure to market conditions.
5. **Philosophy:** Ride winners aggressively, rotate quickly into momentum leaders, let the market tell you what's working.

---

## Top Picks — 评分前列

以下按综合评分排序；是否适合今天买入，还需看上方入场条件。

"""

    # Top picks table
    top = ranked[:20]
    report += "| Rank | Ticker | Name | Score | Grade | Recommendation | Confidence | Regime |\n"
    report += "|------|--------|------|-------|-------|----------------|------------|--------|\n"

    for i, r in enumerate(top, 1):
        sr = r["score_result"]
        name = r.get("name", r["ticker"])[:20]
        report += (
            f"| {i} | **{r['ticker']}** | {name} "
            f"| {sr['score']:.1f} | {sr['grade']} "
            f"| {sr['recommendation']} "
            f"| {sr['confidence']*100:.0f}% "
            f"| {sr.get('regime', '-')} |\n"
        )

    # Component breakdown for top picks
    report += "\n### Score Breakdown (Top Picks)\n\n"
    report += "| Ticker | Technical | Trend | Alpha | Sentiment | Final |\n"
    report += "|--------|-----------|-------|-------|-----------|-------|\n"

    for r in top:
        sr = r["score_result"]
        c = sr["components"]
        report += (
            f"| **{r['ticker']}** "
            f"| {c['technical']['score']:.0f} "
            f"| {c['trend']['score']:.0f} "
            f"| {c['alpha']['score']:.0f} "
            f"| {c['sentiment']['score']:.0f} "
            f"| **{sr['score']:.1f}** |\n"
        )

    # Price performance for top picks
    report += "\n### Price Performance (Top Picks)\n\n"
    report += "| Ticker | Price | 1D | 5D | 1M | 3M | RSI | SMA50 | SMA200 |\n"
    report += "|--------|-------|-----|-----|-----|-----|-----|-------|--------|\n"

    for r in top:
        ind = r.get("indicators", {})
        report += (
            f"| **{r['ticker']}** "
            f"| ${ind.get('price', 0):.2f} "
            f"| {ind.get('change_1d', 0):+.1f}% "
            f"| {ind.get('change_5d', 0):+.1f}% "
            f"| {ind.get('change_1m', 0):+.1f}% "
            f"| {ind.get('change_3m', 0):+.1f}% "
            f"| {ind.get('rsi', 0):.0f} "
            f"| ${ind.get('sma_50', 0):.2f} "
            f"| ${ind.get('sma_200', 0):.2f} |\n"
        )

    # Portfolio weight suggestions (score + momentum + macro)
    report += _build_portfolio_weights(ranked, macro_result)

    # Bottom 20 - Stocks to Sell/Avoid (placed before detailed analysis for visibility)
    report += _build_bottom_20(ranked)

    # Stock discovery section (weekly, on Mondays)
    if discovery_result and discovery_result.get("report"):
        report += discovery_result["report"]

    # Detailed analysis
    report += "\n---\n\n## Detailed Analysis\n\n"

    for r in ranked:
        sr = r["score_result"]
        ind = r.get("indicators", {})
        name = r.get("name", r["ticker"])
        sector = r.get("sector", "Unknown")

        report += f"### {r['ticker']} - {name}\n\n"
        report += f"**Sector:** {sector} | "
        report += f"**Score:** {sr['score']:.1f}/100 ({sr['grade']}) | "
        report += f"**Recommendation:** {sr['recommendation']} | "
        report += f"**Confidence:** {sr['confidence']*100:.0f}%\n\n"

        # Component scores
        c = sr["components"]
        report += "| Component | Score | Confidence | Weight |\n"
        report += "|-----------|-------|------------|--------|\n"
        for comp_name, comp_data in c.items():
            report += (
                f"| {comp_name.title()} "
                f"| {comp_data['score']:.0f}/100 "
                f"| {comp_data['confidence']*100:.0f}% "
                f"| {comp_data['weight']*100:.0f}% |\n"
            )

        # Key indicators
        if ind:
            report += f"\n**Price:** ${ind.get('price', 0):.2f}"
            if ind.get("change_1d") is not None:
                report += f" | **1D:** {ind.get('change_1d', 0):+.1f}%"
            if ind.get("change_1m") is not None:
                report += f" | **1M:** {ind.get('change_1m', 0):+.1f}%"
            if ind.get("change_3m") is not None:
                report += f" | **3M:** {ind.get('change_3m', 0):+.1f}%"
            if ind.get("rsi") is not None:
                report += f" | **RSI:** {ind.get('rsi', 0):.0f}"
            report += "\n"

        report += _compact_stock_news(r)

        # Trend outlook from Claude synthesis
        trend_detail = sr.get("trend_detail", {})
        claude_synth = trend_detail.get("claude_synthesis", {})
        if claude_synth:
            report += "\n**Macro Trend Outlook:**\n"
            if claude_synth.get("regime_assessment"):
                report += f"- **Regime:** {claude_synth['regime_assessment']}\n"
            if claude_synth.get("momentum_assessment"):
                report += f"- **Momentum:** {claude_synth['momentum_assessment']}\n"
            if claude_synth.get("risk_assessment"):
                report += f"- **Risk:** {claude_synth['risk_assessment']}\n"
            if claude_synth.get("outlook"):
                report += f"- **Outlook:** {claude_synth['outlook']}\n"

        # Reasoning
        reasoning = sr.get("reasoning", [])
        if reasoning:
            report += "\n**Key Factors:**\n"
            for reason in reasoning:
                report += f"- {reason}\n"

        report += "\n---\n\n"

    # Footer
    report += """## Disclaimer

- This analysis is for informational purposes only, not financial advice.
- Past performance does not guarantee future results.
- Scores are based on quantitative analysis of technical, trend, sentiment, and alpha factors.
- Always do your own research before making investment decisions.
- Strategy is designed for macro buy-and-hold (weeks+ holding period).

---

*Generated by Trading Bot Recommendation Engine*
"""
    from report_format import format_markdown
    return format_markdown(report)


def _build_macro_section(macro_result):
    """Build the daily macro assessment section."""
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
    section += "| Rank | Ticker | Name | Score | Grade | Recommendation | 主要因素 |\n"
    section += "|------|--------|------|-------|-------|----------------|-----------|\n"

    for i, r in enumerate(bottom, 1):
        sr = r["score_result"]
        name = r.get("name", r["ticker"])[:20]
        reasons = sr.get("reasoning", [])
        issue = reasons[0] if reasons else "暂无原因摘要"
        if len(issue) > 80:
            issue = issue[:79] + "…"
        section += (
            f"| {i} | **{r['ticker']}** | {name} "
            f"| {sr['score']:.1f} | {sr['grade']} "
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

    Strategy V6 (weight math lives in strategy.py, shared with live trading):
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

    # --- Macro cash allocation (canonical, shared with live trading) ---
    macro_score = macro_result.get("score", 50) if macro_result else 50
    macro_rec = macro_result.get("recommendation", "Neutral") if macro_result else "Neutral"
    cash_pct = strategy.macro_cash_pct(macro_score)
    cash_label = strategy.macro_cash_label(cash_pct)

    # --- Momentum composite per stock (for display + selection) ---
    mom_data = {}
    for r in ranked:
        ind = r.get("indicators", {})
        m1 = ind.get("change_1m") / 100 if ind.get("change_1m") is not None else 0.0
        m3 = ind.get("change_3m") / 100 if ind.get("change_3m") is not None else 0.0
        mom_data[r["ticker"]] = strategy.momentum_composite(m1, m3)

    # --- Target weights via the single source of truth ---
    stocks = [{"ticker": r["ticker"], "score": r["score_result"]["score"],
               "mom": mom_data[r["ticker"]]} for r in ranked]
    weights_pct, cash_pct = strategy.compute_target_weights(stocks, macro_score=macro_score)
    cash_label = f"{cash_pct*100:.1f}% (includes capital left unallocated by position caps)"
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
    section += f"**Strategy V6:** Concentrated Top-{top_n} by Momentum | "
    section += "95% momentum + 5% score | "
    section += f"Max position: {max_pos:.0f}%\n\n"
    section += "Top 10 stocks selected by **momentum rank** (3-month + 1-month composite). "
    section += "Capital weighted toward the strongest momentum names with score as a minor quality tilt. "
    section += "Stocks outside top 10 are excluded (0% weight).\n\n"
    section += "This allocation assumes a fresh portfolio; live trading also applies its existing-holding rank buffer.\n\n"

    # Top holdings table (the ones that get allocation)
    allocated = [t for t in sorted_stocks if final_weights[t] > 0.1]
    not_allocated = [t for t in sorted_stocks if final_weights[t] <= 0.1]

    section += f"### Portfolio Holdings ({len(allocated)} stocks)\n\n"
    section += "| Rank | Ticker | Name | Weight | Score | 1M | 3M | Mom Rank |\n"
    section += "|------|--------|------|--------|-------|-----|-----|----------|\n"

    for i, t in enumerate(allocated, 1):
        r = next(x for x in ranked if x["ticker"] == t)
        sr = r["score_result"]
        ind = r.get("indicators", {})
        name = r.get("name", t)[:18]
        w = final_weights[t]
        m1 = ind.get("change_1m", 0)
        m3 = ind.get("change_3m", 0)
        mom_rank = top_by_mom.index(t) + 1 if t in top_by_mom else "-"

        section += (
            f"| {i} | **{t}** | {name} "
            f"| {w:.1f}% "
            f"| {sr['score']:.1f} "
            f"| {m1:+.1f}% "
            f"| {m3:+.1f}% "
            f"| #{mom_rank} |\n"
        )

    # Show total allocated
    total_allocated = sum(final_weights[t] for t in allocated)
    section += f"\n**Total Invested:** {total_allocated:.1f}% | "
    section += f"**Cash:** {cash_pct*100:.0f}%\n"

    # Not allocated (excluded from portfolio)
    if not_allocated:
        section += f"\n### Excluded from Portfolio ({len(not_allocated)} stocks — 0% weight)\n\n"
        section += "| Ticker | Name | Score | Recommendation | Reason |\n"
        section += "|--------|------|-------|----------------|--------|\n"
        for t in not_allocated[:30]:
            r = next(x for x in ranked if x["ticker"] == t)
            sr = r["score_result"]
            name = r.get("name", t)[:18]
            section += (
                f"| {t} | {name} "
                f"| {sr['score']:.1f} "
                f"| {sr['recommendation']} "
                f"| Outside top {top_n} by momentum |\n"
            )
        if len(not_allocated) > 30:
            section += f"\n*...and {len(not_allocated) - 30} more excluded stocks*\n"

    section += "\n"
    return section


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
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|------|\n"
        for r in leaders:
            section += _ai_semi_row(r, cfg_sector(r["ticker"]))
        section += "\n**落后 Bottom 5：**\n\n"
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|------|\n"
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
    return (
        f"| **{r['ticker']}** | {name} | {sub_sector} "
        f"| {sr['score']:.1f} | {m1_str} | {m3_str} | {rec} |\n"
    )


def _fmt_pct(val, digits=1):
    if val is None:
        return "—"
    try:
        return f"{float(val):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def _build_stock_trend_section(ranked, macro_result=None):
    """General block: current trend, 1w/1m/6m outlook, add-vs-sell stance."""
    import macro_analyzer as ma

    market_data = (macro_result or {}).get("market_data") or {}
    trend = (macro_result or {}).get("trend") or ma.classify_market_trend(market_data)
    outlook = (macro_result or {}).get("trend_outlook")
    if not outlook:
        score = (macro_result or {}).get("score", 50)
        outlook = ma.fallback_trend_outlook(trend, score, market_data)

    stance = outlook.get("stance", "hold")
    stance_label = outlook.get("stance_label") or ma.STANCE_LABELS.get(stance, stance)

    spy_dd = _fmt_pct(trend.get("spy_from_high"))
    qqq_dd = _fmt_pct(trend.get("qqq_from_high"))
    smh_dd = _fmt_pct(trend.get("smh_from_high"))
    vix = trend.get("vix")
    vix_s = f"{vix:.1f}" if isinstance(vix, (int, float)) else "—"
    sma200 = "上方" if trend.get("above_sma200") else (
        "下方" if trend.get("above_sma200") is False else "未知"
    )

    def _row(label, horizon):
        h = horizon or {}
        direction = h.get("direction", "range")
        dir_label = ma.HORIZON_DIR_LABELS.get(direction, direction)
        view = h.get("view") or "—"
        return f"| {label} | {dir_label} | {view} |\n"

    section = "## General — 美股趋势判断\n\n"
    section += (
        f"**当前趋势：** {trend.get('label', '—')} · "
        f"{trend.get('structure_label', '')}\n\n"
    )
    section += (
        f"标普（SPY）距近一年高点 {spy_dd} | 纳指 {qqq_dd} | 半导体 SMH {smh_dd} | "
        f"VIX {vix_s} | 标普相对 200 日均线：{sma200}\n\n"
    )
    if outlook.get("current_trend"):
        section += f"{outlook['current_trend']}\n\n"

    section += f"**指数仓位建议：{stance_label}**\n\n"
    if outlook.get("sizing_guidance"):
        section += f"{outlook['sizing_guidance']}\n\n"

    section += "| 期限 | 方向 | 判断 |\n"
    section += "|------|------|------|\n"
    section += _row("未来一周", outlook.get("outlook_1w"))
    section += _row("未来一个月", outlook.get("outlook_1m"))
    section += _row("未来半年", outlook.get("outlook_6m"))
    section += "\n"

    section += (
        "对照：尽量卖出 = 降低风险预算、停止新开仓；"
        "大量抄底 = 只在指数已有双位数回撤且波动率恐慌时才考虑。"
        "普通回撤默认分批，不把计划资金一次打完。"
        "个股买卖看下方「今日建议买入」——宏观持有不等于每只都不能买。\n"
    )

    import cash_entry_plan
    cash_plan = (macro_result or {}).get('cash_entry_plan') or cash_entry_plan.from_market_data(market_data)
    section += "\n" + cash_entry_plan.render(cash_plan)

    # Keep a compact breadth read from the scored universe
    if ranked:
        regimes = {}
        trend_scores = []
        for r in ranked:
            sr = r["score_result"]
            regime = sr.get("regime", "Unknown")
            regimes[regime] = regimes.get(regime, 0) + 1
            comp = sr.get("components", {})
            if "trend" in comp:
                trend_scores.append(comp["trend"]["score"])
        total = len(ranked)
        avg_trend = sum(trend_scores) / len(trend_scores) if trend_scores else 50
        section += (
            f"\n**股票池内部：** 平均趋势分 {avg_trend:.0f}/100。"
            "Regime 分布："
        )
        bits = []
        for regime, count in sorted(regimes.items(), key=lambda x: -x[1])[:5]:
            bits.append(f"{regime} {count / total * 100:.0f}%")
        section += "；".join(bits) + "\n"

    section += "\n---\n"
    return section


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
