"""Scheduled macro / earnings catalysts for the Regime Desk (macro_regime).

Hand-maintained. Each entry: (ISO date, label, stars 1-3, note). The desk shows
the next `n` upcoming entries and warns in the report when the list is about
to run out (fewer than MIN_FORWARD_DAYS of coverage), so a stale calendar is
visible instead of silently empty. Times are US/Eastern where they matter.

Last refreshed: 2026-09-29 (Q4 2026 through mid-Jan 2027).
"""
from datetime import date, datetime

MIN_FORWARD_DAYS = 14

EVENTS = [
    ("2026-09-30", "PCE (Aug) 08:30 + MU FQ4 AMC", 2, "PCE 决定 10 月加息概率；MU 看 HBM 需求"),
    ("2026-10-01", "ISM 制造业 + 10y/30y 拍卖公告", 2, "物价分项影响加息预期"),
    ("2026-10-02", "非农 (Sep) 08:30", 3, "共识 +90k / 失业率 4.1%；强数据锁定 10 月加息"),
    ("2026-10-04", "OPEC+ 8 国月度会议", 1, "油价已因伊朗抬升"),
    ("2026-10-05", "ISM 服务业", 2, "服务通胀脉搏"),
    ("2026-10-07", "FOMC 会议纪要 (9/15-16) 14:00 + 10y 拍卖", 2, "看多少委员倾向更快加息；5.2% 上拍卖是压力测试"),
    ("2026-10-08", "30y 拍卖", 2, "长端需求"),
    ("2026-10-09", "密歇根消费者信心 (初值)", 1, "通胀预期"),
    ("2026-10-13", "JPM / GS / WFC / C 财报 BMO（暂定）", 2, "财报季开场"),
    ("2026-10-14", "CPI (Sep) 08:30 + ASML 财报 + BAC/MS", 3, "FOMC 前最后一份 CPI；ASML 订单看半导体"),
    ("2026-10-15", "PPI + 零售销售 + TSM 财报", 3, "TSM 的 AI 需求/资本开支指引定半导体基调"),
    ("2026-10-16", "月度期权到期", 2, "大型科技财报前 gamma 释放"),
    ("2026-10-20", "NFLX 财报 AMC", 1, "首个大型成长股财报"),
    ("2026-10-21", "TSLA 财报 AMC（暂定）", 2, ""),
    ("2026-10-28", "FOMC 决议 14:00 + Warsh 记者会；MSFT/GOOGL/META 财报 AMC（暂定）", 3, "第二次加息约 75% 定价；同晚 2027 资本开支指引 —— 本季最高风险日"),
    ("2026-10-29", "Q3 GDP 初值 + PCE (Sep) + AAPL/AMZN 财报 AMC + ECB + BOJ (29-30)", 3, "本季密度最高的一天"),
    ("2026-10-30", "月末 + 大型科技财报后再平衡", 2, "历史上波动大的跟随日"),
    ("2026-11-02", "ISM 制造业 + PLTR 财报 AMC", 2, "高估值 AI 风向标"),
    ("2026-11-03", "美国中期选举 + JOLTS + AMD 财报 AMC", 3, "分裂国会 → 财政僵局；选后季节性通常偏多"),
    ("2026-11-04", "财政部季度再融资 08:30 + ISM 服务业", 3, "5% 以上收益率时任何增发都是冲击"),
    ("2026-11-05", "BOE 决议", 1, ""),
    ("2026-11-06", "非农 (Oct) 08:30", 3, "首个加息后的就业数据"),
    ("2026-11-10", "CPI (Oct) 08:30 + 10y 拍卖", 3, ""),
    ("2026-11-13", "PPI (Oct)", 1, ""),
    ("2026-11-17", "NVDA FQ3 财报 AMC + 零售销售", 3, "单一最大指数级催化剂；感恩节前流动性偏薄"),
    ("2026-11-18", "FOMC 会议纪要 (Oct) 14:00", 2, "12 月加息讨论"),
    ("2026-11-20", "月度期权到期", 1, ""),
    ("2026-11-25", "GDP 二估 + PCE (Oct)；感恩节 11/26", 2, "流动性薄"),
    ("2026-12-01", "JOLTS + ISM 制造业 + CRWD 财报 AMC（估）", 1, ""),
    ("2026-12-04", "非农 (Nov) 08:30", 3, "12 月 FOMC 前最后一份就业"),
    ("2026-12-09", "FOMC 决议 + 点阵图/SEP + Warsh 记者会", 3, "2027 利率路径；终端利率"),
    ("2026-12-10", "CPI (Nov) 08:30 + AVGO FQ4 财报 AMC（估）", 3, "AI ASIC 指引 + FOMC 次日 CPI"),
    ("2026-12-11", "临时拨款到期 → 政府关门风险", 2, "跛脚鸭国会；数据延迟风险"),
    ("2026-12-14", "ORCL FQ2 财报 AMC（估）", 2, "债务驱动资本开支的代理指标"),
    ("2026-12-15", "PPI (Nov)", 1, ""),
    ("2026-12-16", "MU FQ1 财报 AMC（估）", 2, ""),
    ("2026-12-17", "ECB / BOE / BOJ (17-18) 决议", 2, "BOJ 加息 → 日元套利平仓风险"),
    ("2026-12-18", "四巫日 + 标普季度再平衡 + 纳指 100 重构", 2, "全年成交量最大的一天"),
    ("2026-12-30", "FOMC 会议纪要 (Dec)", 1, ""),
    ("2027-01-10", "美中贸易休战到期（已从 11/10 延长两个月）", 3, "关税回弹 / 稀土风险笼罩 12 月"),
]


def upcoming(n=8, today=None):
    """Next n events on/after today as (date, label, stars, note)."""
    today = today or datetime.now().date()
    out = []
    for d, label, stars, note in EVENTS:
        dd = date.fromisoformat(d)
        if dd >= today:
            out.append((dd, label, stars, note))
    return out[:n]


def coverage_days(today=None):
    """Days of calendar coverage remaining; 0 when the list is exhausted."""
    today = today or datetime.now().date()
    future = [date.fromisoformat(d) for d, *_ in EVENTS if date.fromisoformat(d) >= today]
    return (max(future) - today).days if future else 0
