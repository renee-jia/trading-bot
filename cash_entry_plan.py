"""Report-only entry plan for the user's separate cash pool.

Everything is a percentage of that pool (0-100). The pool size is never written
into code, configuration or the report; the reader converts locally.
Cumulative targets, never broker orders.
"""
from datetime import date, timedelta
import math

import numpy as np
import pandas as pd

PLAN_START = date(2026, 9, 8)
REVIEW_DATE = date(2026, 10, 15)
FULL = 100.0        # the whole pool, in percent
WEEKLY_STEP = 12.5  # time rule: cumulative target added per elapsed week
BATCH_CAP = 25.0    # one review batch never exceeds this share of the pool
# name, minimum VIX, minimum drawdown %, cumulative deployment target (% of pool)
TIERS = (
    ('普通回调',18,3,12.5),
    ('第一买点',20,5,31.25),
    ('⭐ 好买点',23,8,56.25),
    ('⭐⭐ 恐慌',27,10,81.25),
    ('⭐⭐⭐ 大跌',30,15,100.0),
)


def _p(v):
    """Percent-of-pool formatter: 12.5 -> '12.5%', 100.0 -> '100%'."""
    return f"{v:g}%"


def _session_dates(index):
    """Preserve local session dates, including CSV indices spanning DST offsets."""
    return pd.DatetimeIndex([pd.Timestamp(value).tz_localize(None).normalize()
                             for value in index])


def completed_daily_history(frame, now=None):
    """Exclude an unfinished US daily bar; early closes are treated conservatively."""
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo('America/New_York'))
    now = now.astimezone(ZoneInfo('America/New_York'))
    if frame is None or frame.empty or now.time() >= time(16):
        return frame
    dates = _session_dates(frame.index).date
    return frame.loc[dates < now.date()]


def snapshot_from_history(spx, vix, as_of=None):
    """Use a shared session; seed peak with 63 closes ending at plan inception.

    After inception the reference peak never rolls down during this cash plan.
    SPX is ^GSPC (price index), not SPY total-return-adjusted prices.
    """
    as_of = as_of or date.today()
    def close_series(frame):
        if frame is None or frame.empty or 'Close' not in frame:
            return pd.Series(dtype=float)
        s = pd.to_numeric(frame['Close'],errors='coerce').copy()
        s.index = _session_dates(s.index)
        s = s[~s.index.duplicated(keep='last')].sort_index()
        s = s.loc[:pd.Timestamp(as_of)]
        return s.where(np.isfinite(s) & (s>0))
    s,v=close_series(spx),close_series(vix)
    if s.empty or v.empty:
        return {'status':'unavailable','reason':'缺少 SPX 或 VIX 行情'}
    if s.index[-1] != v.index[-1]:
        return {'status':'unavailable','reason':'SPX 与 VIX 最新交易日不一致'}
    common = s.index.intersection(v.index)
    s,v=s.reindex(common),v.reindex(common)
    if common.empty or s.tail(63).isna().any() or v.tail(6).isna().any():
        return {'status':'unavailable','reason':'行情缺失，无法确认回撤与波动率'}
    day=common[-1].date()
    if not 0 <= (as_of-day).days <= 4:
        return {'status':'unavailable','reason':'行情过旧，暂停计算资金动作'}
    if as_of < PLAN_START:
        return {'status':'unavailable','reason':'尚未到本计划开始日期'}
    initial=s.loc[:pd.Timestamp(PLAN_START)].tail(63)
    if len(initial)<63 or (PLAN_START-initial.index[-1].date()).days>4:
        return {'status':'unavailable','reason':'不足计划起点前63个交易日，无法建立高点基准'}
    initial_peak=float(initial.max())
    path=s.loc[pd.Timestamp(PLAN_START):]
    if path.empty or path.isna().any():
        return {'status':'unavailable','reason':'缺少计划开始后的市场路径'}
    peak_path=path.cummax().clip(lower=initial_peak)
    dd_path=(path/peak_path-1)*100
    peak=float(peak_path.iloc[-1])
    base=s.loc[:path.index[-1]]
    peak_date=base[base==peak].index[-1].date().isoformat()
    latest=float(s.iloc[-1]); level=float(v.iloc[-1])
    return {'status':'ok','as_of':day.isoformat(),'source':'Yahoo Finance ^GSPC / ^VIX daily close',
            'spx':latest,'vix':level,'peak':peak,'peak_date':peak_date,
            'drawdown_pct':float(dd_path.iloc[-1]),
            'rolling_63_drawdown_pct':float((latest/s.tail(63).max()-1)*100),
            'drawdown_252_pct':float((latest/s.tail(252).max()-1)*100),
            'vix_change_5d_pct':float((v.iloc[-1]/v.iloc[-6]-1)*100) if len(v)>=6 else None,
            'dip_seen_since_start':bool((dd_path<=-3).any()),
            'worst_drawdown_since_start':float(dd_path.min()),
            'vix_implied_30d_move_pct':level*math.sqrt(30/365)}


def evaluate(snapshot, deployed_pct=0, today=None, capital_as_of=PLAN_START):
    """deployed_pct is the cumulative *confirmed* spend, as a percent of the pool.

    Target minus declared spend is an outstanding plan gap, never a daily order.
    Repeated runs cannot increment this number or assume earlier orders happened.
    """
    today=today or date.today()
    deployed=deployed_pct
    if not isinstance(deployed,(int,float)) or not math.isfinite(deployed) or not 0<=deployed<=FULL:
        raise ValueError('累计已投入比例必须介于 0 与 100 之间')
    result={'status':'wait','tier':'等待','target_pct':deployed,'declared_pct':deployed,
            'declared_cash_pct':FULL-deployed,'target_cash_pct':FULL-deployed,
            'gap_pct':0,'next_batch_cap_pct':0,'capital_as_of':capital_as_of.isoformat(),
            'capital_needs_confirmation':capital_as_of!=today,
            'reason':'','snapshot':snapshot}
    if snapshot.get('status')!='ok':
        result['status']='unavailable';result['reason']=snapshot.get('reason','行情不可用')
        return result
    try:
        quote_day=date.fromisoformat(snapshot['as_of'])
        if not 0 <= (today-quote_day).days <= 4:
            raise ValueError('stale')
    except (KeyError,TypeError,ValueError):
        result['status']='unavailable';result['reason']='行情日期缺失、未来日期或过旧，暂停资金动作'
        return result
    vix,dd=snapshot.get('vix'),snapshot.get('drawdown_pct')
    if any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in (vix,dd)) or vix<=0 or dd>0:
        result['status']='unavailable';result['reason']='VIX 或回撤数据无效'
        return result
    if today<PLAN_START:
        result['reason']='计划尚未开始'
        return result
    # All lower bounds are inclusive; upper range labels are descriptive only.
    matched=[t for t in TIERS if vix>=t[1] and -dd>=t[2]]
    if matched:
        tier=matched[-1]
        result.update(tier=tier[0],target_pct=tier[3],status='conditional')
        result['reason']='VIX 与 SPX 回撤同时达到该档下限；比例为累计投入目标。'
    elif today>=REVIEW_DATE and vix<18 and -dd<3 and snapshot.get('dip_seen_since_start') is False:
        weeks=(today-REVIEW_DATE).days//7+1
        result.update(tier='时间止等：逐步买回',target_pct=min(FULL,weeks*WEEKLY_STEP),status='conditional')
        result['reason']=f'已到10月15日，计划以来未出现3%回撤；改为每周累计增加现金池 {_p(WEEKLY_STEP)} 的目标，仍按已投入比例补差。'
    else:
        vix_tier=max([i for i,t in enumerate(TIERS,1) if vix>=t[1]],default=0)
        dd_tier=max([i for i,t in enumerate(TIERS,1) if -dd>=t[2]],default=0)
        if vix_tier!=dd_tier:
            result['reason']='VIX 与回撤不同步：不因单独恐慌或单独跌幅直接升级投入档位。'
        elif vix<18 and -dd<3:
            result['reason']='波动较低、指数接近高位；按计划不急，保留现金等价格折扣。'
        else:
            result['reason']='尚未满足联合触发条件。'
        if today>=REVIEW_DATE and snapshot.get('dip_seen_since_start') is not False:
            result['reason']+='期间曾发生回撤或路径未确认，不自动套用“始终没跌”的时间规则；复核已执行金额。'
    # Never imply selling to restore cash if a rebound downgrades the current tier.
    result['target_pct']=max(deployed,result['target_pct'])
    result['target_cash_pct']=FULL-result['target_pct']
    result['gap_pct']=max(0,result['target_pct']-deployed)
    result['next_batch_cap_pct']=min(BATCH_CAP,result['gap_pct'])
    if not result['gap_pct'] and result['status']=='conditional':
        result['status']='funded';result['reason']+='声明已投入比例已达到目标，不重复买入。'
    if result['gap_pct']>BATCH_CAP:
        result['reason']+=f'跨档跳跌时不一次补齐：单轮参考上限为现金池 {_p(BATCH_CAP)}，余款分步复核。'
    return result


def render(plan):
    snap=plan['snapshot']
    lines=['### 现金入场计划 — VIX × SPX 分批入场\n',
           '这是单独的现金入场预算，全部按现金池百分比表示（金额自行换算，不写入报告）；比例是累计计划，不是 Alpaca 账户余额或自动交易指令。\n',
           '| 市场条件 | VIX 参考 | SPX 距高点 | 该档新增 | 累计投入目标 | 目标剩余现金 |',
           '|---|---|---|---|---|---|',
           '| 9/8 用户起始参考 | 15–16 | 接近高位 | 不急 | 0% | 100% |',
           '| 普通回调 | 18–20 | −3%～−5% | 12.5% | 12.5% | 87.5% |',
           '| 第一买点 | 20–23 | −5%～−7% | 18.75% | 31.25% | 68.75% |',
           '| ⭐ 好买点 | 23–27 | −8%～−10% | 25% | 56.25% | 43.75% |',
           '| ⭐⭐ 恐慌 | 27–32 | −10%～−15% | 25% | 81.25% | 18.75% |',
           '| ⭐⭐⭐ 大跌 | ≥30 | ≤−15% | 剩余 18.75% | 100% | 0% |',
           '| 10/15 起仍未回调 | <18 | 0～−3%以内 | 每周 12.5% 参考 | 按周递增 | 逐步下降 |','']
    if snap.get('status')=='ok':
        lines += [f"**当前读数（{snap['as_of']}）：VIX {snap['vix']:.2f}；SPX {snap['spx']:,.2f}；"
                  f"距计划高点 {snap['peak']:,.2f}（{snap['peak_date']}）{snap['drawdown_pct']:+.2f}%。**",
                  f"近63日滚动高点回撤 {snap['rolling_63_drawdown_pct']:+.2f}%；近252日高点回撤 {snap['drawdown_252_pct']:+.2f}%。",
                  f"VIX 对应约30天波动幅度参考 ±{snap['vix_implied_30d_move_pct']:.1f}%（年化值换算，不是预测跌幅或底部概率）。"]
        if snap.get('vix_change_5d_pct') is not None:
            lines.append(f"VIX 过去5个交易日变化 {snap['vix_change_5d_pct']:+.1f}%；判断是否仍在升温，而不是只看绝对数值。")
    if snap.get('status')=='ok':
        levels='；'.join(f"−{t[2]}% ≈ {snap['peak']*(1-t[2]/100):,.0f}（VIX≥{t[1]}）" for t in TIERS)
        lines += ['', '按当前参考高点换算的 SPX 档位：'+levels+'。创新高后随之上调。']
    lines += ['', f"**当前档位：{plan['tier']}。** {plan['reason']}",
              f"累计投入目标 **{_p(plan['target_pct'])}**；目标剩余现金 **{_p(plan['target_cash_pct'])}**（均为现金池比例）。",
              f"截至 {plan['capital_as_of']} 声明已投入 {_p(plan['declared_pct'])}，"
              f"声明剩余 {_p(plan['declared_cash_pct'])}；与当前目标差额 {_p(plan['gap_pct'])}。"]
    if plan['gap_pct']:
        lines.append(f"若此前投入记录仍准确，本轮参考不超过现金池 {_p(plan['next_batch_cap_pct'])}；重复报告不代表再买同一笔。")
    if plan['capital_needs_confirmation']:
        lines.append('资金状态不是今日确认值，实际执行前先更新已投入记录；不会把历史声明当成实时可用现金。')
    lines += ['', '执行解释：两项达到下限才进入该档；区间上限不导致退出，7%～8%等间隔沿用已满足的较低档。',
              '高点取9/8起点前63个交易日最高收盘价，此后创新高上调，旧高点移出窗口不下调；SPX 使用指数 ^GSPC，不用 SPY 复权价替代。',
              'VIX 高反映期权市场预期波动大，并不确认底部。恐慌买点仍是分批风险预算；指数现金计划不能直接转成买 call 或卖 put 指令。',
              '若 VIX 升高但指数尚未充分回撤，先等价格；若指数已回撤而 VIX 回落，单独复核企稳与已执行资金，不机械打满。\n']
    return '\n\n'.join(lines[:2])+'\n'+ '\n'.join(lines[2:])+'\n'


def from_market_data(market_data, today=None):
    """Read declared plan spend; never infer it from the bot's unrelated account."""
    import os
    snapshot=((market_data or {}).get('^GSPC') or {}).get('cash_plan_snapshot') or {
        'status':'unavailable','reason':'未取得 SPX/VIX 同期行情；不以 SPY 替代'}
    try:
        deployed=float(os.environ.get('CASH_PLAN_DEPLOYED_PCT','0'))
        stamp=date.fromisoformat(os.environ.get('CASH_PLAN_CAPITAL_AS_OF',PLAN_START.isoformat()))
        if stamp>(today or date.today()) or stamp<PLAN_START:
            raise ValueError('资金确认日期不在有效范围')
        return evaluate(snapshot,deployed_pct=deployed,today=today,capital_as_of=stamp)
    except (TypeError,ValueError):
        return evaluate({'status':'unavailable','reason':'现金计划资金配置无效，先核对已投入比例和日期'},today=today)


def email_lines(plan):
    lines=['=== 现金入场计划 ===',f"档位：{plan['tier']}；{plan['reason']}"]
    snap=plan['snapshot']
    if snap.get('status')=='ok':
        lines.append(f"{snap['as_of']}：VIX {snap['vix']:.2f}，SPX距计划高点 {snap['drawdown_pct']:+.2f}%")
    lines.append(f"累计投入目标 {_p(plan['target_pct'])}；目标剩余现金 {_p(plan['target_cash_pct'])}（现金池比例）。非每日重复买入金额。")
    return lines
