"""Presentation-only report roll-ups; no market fetches or trading decisions."""
import re


def table(headers, rows):
    def cell(value):
        return str(value if value is not None else '—').replace('|', r'\|').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |',
                      '|' + '|'.join(['---'] * len(headers)) + '|'] +
                     ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]) + '\n'


def unique(values):
    return list(dict.fromkeys(str(v).strip() for v in values if v and str(v).strip()))


def subsection(markdown):
    """Nest an existing desk under an appendix without creating new top-level sections."""
    text = re.sub(r'^---\s*$', '', markdown or '', flags=re.M).strip()
    return re.sub(r'^(#{2,5}) ', r'\1# ', text, flags=re.M) + '\n' if text else ''


def remove_subsection(markdown, heading):
    """Remove one level-3 block whose content is shown in another desk."""
    return re.sub(r'^### ' + re.escape(heading) + r'[^\n]*\n.*?(?=^### |^## |\Z)',
                  '', markdown, flags=re.M | re.S)


def theme_entries(books):
    """One row per symbol; keep membership and each desk's unique reference fields."""
    entries = {}
    for label, data in books:
        for key in ('names', 'watch', 'priority'):
            for item in (data or {}).get(key) or []:
                symbol = item['ticker'].upper().replace('BRK.B', 'BRK-B')
                tag = label + ('观察' if key == 'watch' else '')
                entry = entries.setdefault(symbol, {'ticker': symbol, 'item': {}, 'tags': [], 'refs': []})
                if tag not in entry['tags']:
                    entry['tags'].append(tag)
                # A scored row is authoritative for current price/action. Empty
                # fields never erase a reference supplied by another book.
                for field, value in item.items():
                    if value is not None and value != '' and (field not in entry['item'] or item.get('in_run')):
                        entry['item'][field] = value
                refs = []
                if item.get('priority') is not None:
                    refs.append(f"优先级 {item['priority']}：{item.get('reason') or '—'}")
                if item.get('stars'):
                    refs.append(item['stars'])
                refs.extend(item.get(k) for k in ('module', 'layer', 'view'))
                if item.get('risk'):
                    refs.append('风险：' + item['risk'])
                if item.get('zone') and item['zone'] != '—':
                    refs.append(f"首批 {item['zone']}（{item.get('zone_status') or '—'}）")
                if item.get('fwd_pe') and item['fwd_pe'] != '—':
                    refs.append('参考 PE ' + str(item['fwd_pe']))
                if item.get('pt') is not None:
                    refs.append(f"参考目标 ${item['pt']:g}（{item.get('upside') or '—'}）")
                if item.get('open_px') is not None:
                    refs.append(f"开仓 ${item['open_px']:g} / 加仓 ${item['add_px']:g}（{item.get('ladder') or '—'}）")
                entry['refs'] = unique(entry['refs'] + refs)
    return list(entries.values())


def theme_section(books, reference_only=False):
    """Fixed theme views. With reference_only the daily price, score and call
    are left to the stock table, so a name's numbers appear once in the report."""
    entries = theme_entries(books)
    lines = ['## 主题观察 — 固定观点与价位\n' if reference_only else '## 主题观察 — 合并名单\n',
             ('各主题名单的固定观点：星级、优先级、首批区间、开仓/加仓价。同一股票只占一行；'
              '现价、评分和今日判断见「个股总表」，这里不重复。\n') if reference_only else
             ('AI 核心/观察、半导体、光学互联、SaaS、抄底布局和电力统一展示；同一股票只占一行。'
              '星级、优先级、首批区间和开仓/加仓价为固定参考；评分与动作随本次行情更新。\n')]
    summary = []
    for label, data in books:
        if (data or {}).get('error'):
            summary.append([label, '数据不可用', '—', data['error']])
            continue
        s = (data or {}).get('summary') or {}
        avg = f"{s['avg_score']:.0f}" if s.get('avg_score') is not None else '—'
        hits = unique((s.get('in_zone') or []) + (s.get('open_now') or []) + (s.get('add_now') or []))
        summary.append([label, f"{s.get('in_run', 0)}/{s.get('total', 0)}", avg,
                        '、'.join(hits) or '无价格区间触发'])
    lines.append(table(['主题', '已评分/名单', '平均分', '价格区间触发（仍需入场确认）'], summary))
    lines.append('\n### 逐股观察\n')
    if not reference_only:
        lines.append('按主题分组，每只股票只在第一个所属主题下出现一次；本期未纳入评分的名单只列代码。\n')
    money = lambda v: '—' if v is None else f'${v:.2f}'
    pct = lambda v: '—' if v is None else f'{v:+.1f}%'
    for label, _data in books:
        group = [e for e in entries if e['tags'][0].startswith(label)]
        scored = [e for e in group if e['item'].get('in_run')]
        if not group:
            continue
        lines.append(f"#### {label}（已评分 {len(scored)} / 名单 {len(group)}）\n")
        rows, shown = [], set()
        for entry in group:
            x = entry['item']
            name = x.get('display') or entry['ticker']
            # A fixed price reference (zone, open/add level) stays visible even
            # without a score; a bare unscored name does not earn a row.
            pinned = any(k in ref for ref in entry['refs'] for k in ('首批', '开仓', '参考目标'))
            if not x.get('in_run') and not pinned:
                continue
            if reference_only:
                if not entry['refs']:
                    continue
                rows.append([f"**{name}**" + ('' if x.get('in_run') else '（未评分）'),
                             ' / '.join(entry['tags']), '；'.join(entry['refs'])])
            else:
                score = f"{x['score']:.1f}" if x.get('score') is not None else '—'
                action = x.get('add_verdict') or x.get('buy_label') or '—'
                if not x.get('in_run'):
                    action = '未评分，等待行情确认'
                rows.append([f"**{name}**", ' / '.join(entry['tags']),
                             money(x.get('price')), pct(x.get('change_1d')), score,
                             pct(x.get('from_high')), action, '；'.join(entry['refs']) or '—'])
            shown.add(name)
        if rows:
            lines.append(table(['股票', '所属主题', '固定观点 / 点位'] if reference_only else
                               ['股票', '所属主题', '现价', '1日', '评分', '距高点', '今日判断', '固定观点 / 点位'], rows))
        rest = [e['item'].get('display') or e['ticker'] for e in group
                if (e['item'].get('display') or e['ticker']) not in shown and not e['item'].get('in_run')]
        if rest:
            lines.append(f"本期未纳入评分：{'、'.join(rest)}。\n")
    lines.append('\nAAOI / LYTE 按小市值或 ETF 控制仓位。参考估值与目标价为配置观点，'
                 '不代表当日机构更新；股票和 put 二选一，买点与财报见期权附录，逐股依据见 Detailed Analysis。\n')
    return '\n'.join(lines)


def plain_summary(markdown):
    """Use the same decision desks for text email; no second watch/ranking pipeline."""
    from report_format import decision_markdown
    text, _, _ = decision_markdown(markdown)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)
    text = re.sub(r'(?m)^#{1,6} ', '', text)
    return text.replace('**', '').replace('`', '').strip()
