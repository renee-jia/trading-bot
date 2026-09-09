"""Shared Markdown layout and safe, self-contained web/email report rendering."""
import html
import re

from markdown_it import MarkdownIt


def format_markdown(text):
    """Consistent spacing, with decisions ahead of methodology and research."""
    parts = re.split(r'(?=^## )', text, flags=re.M)
    if len(parts) > 1:
        def priority(part):
            title = part.splitlines()[0]
            groups = [('Executive Summary', 0), ('General', 1), ('Macro Desk', 2),
                      ('今日建议买入', 3), ('Options Desk', 4), ('Options Research', 5),
                      ('Covered Call', 6), ('Cash-secured Put', 7),
                      ('Sell Put 方案', 8),
                      ('Top Picks', 9), ('Suggested Portfolio', 10),
                      ('评分后列', 11), ('Config', 12), ('Top Movers', 13),
                      ('Daily Macro', 14), ('Alpaca', 15), ('AI /', 16),
                      ('个股评分差异', 17), ('Scoring Methodology', 18),
                      ('Portfolio Strategy', 19), ('Detailed Analysis', 20),
                      ('Disclaimer', 21)]
            return next((n for key, n in groups if key in title), 16)
        text = parts[0] + ''.join(sorted(parts[1:], key=priority))
    # CommonMark requires a blank line before lists following a paragraph.
    lines = []
    for line in text.splitlines():
        if re.match(r'^(?:- |\d+\. )', line) and lines and lines[-1].strip():
            if not re.match(r'^(?:- |\d+\. )', lines[-1]):
                lines.append('')
        lines.append(line.rstrip())
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip() + '\n'


STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; background:#edf2f7; color:#243247;
       font:15px/1.75 -apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',Arial,sans-serif; }
main { max-width:1180px; margin:32px auto; padding:36px 44px; background:#fff;
       border-top:6px solid #127c80; border-radius:12px; }
h1 { font-size:30px; line-height:1.3; color:#102b45; margin:0 0 18px; }
h2 { color:#12324c; font-size:23px; margin:42px 0 18px; padding-top:14px;
     border-top:1px solid #dce5ef; scroll-margin-top:20px; }
h3 { font-size:18px; margin:26px 0 12px; color:#17686e; }
p { margin:12px 0; } a { color:#096eaa; text-decoration:none; }
a:hover { text-decoration:underline; } strong { font-weight:650; }
blockquote { margin:20px 0; padding:10px 18px; border-left:4px solid #127c80;
             background:#edf8f7; border-radius:0 8px 8px 0; }
.table-scroll { overflow-x:auto; margin:18px 0; border:1px solid #dce5ef; border-radius:8px; }
table { border-collapse:collapse; width:100%; font-size:13px; font-variant-numeric:tabular-nums; }
th { background:#17364e; color:white; font-weight:600; text-align:left; }
td,th { padding:10px 12px; border-bottom:1px solid #e2e8f0; vertical-align:top; min-width:75px; }
tbody tr:nth-child(even) { background:#f5f8fb; } tbody tr:hover { background:#edf8f7; }
td:first-child { font-weight:600; } td { overflow-wrap:break-word; }
hr { border:0; border-top:1px solid #e4ebf2; margin:24px 0; }
code { background:#eef2f6; padding:2px 5px; border-radius:4px; font-size:.9em; }
nav { background:#f5f8fb; border:1px solid #dce5ef; border-radius:8px; padding:16px 20px; }
nav ul { columns:2; margin:8px 0 0; padding-left:20px; }
nav a { font-size:13px; } details { border:1px solid #dce5ef; border-radius:8px; margin:12px 0; padding:12px 16px; }
summary { cursor:pointer; color:#12324c; font-weight:650; }
@media(max-width:680px) { main { margin:0; padding:22px 16px; border-radius:0; }
 h1 { font-size:25px; } h2 { font-size:21px; } nav ul { columns:1; } td,th { padding:8px; } }
@media print { body { background:white; } main { margin:0; padding:0; max-width:none; }
 nav { display:none; } .table-scroll { overflow:visible; } h2,h3 { break-after:avoid; }
 tr { break-inside:avoid; } details::details-content { content-visibility:visible; } }
"""


def render_html(markdown, *, email=False):
    """Disable raw HTML from external text; use a standards-based Markdown parser."""
    parser = MarkdownIt('commonmark', {'html': False}).enable('table')
    tokens = parser.parse(markdown)
    toc = []
    for i, token in enumerate(tokens):
        if token.type == 'heading_open' and token.tag == 'h2':
            anchor = f'section-{len(toc)+1}'
            token.attrSet('id', anchor)
            toc.append((anchor, tokens[i+1].content))
    body = parser.renderer.render(tokens, parser.options, {})
    # Web gets collapsible per-stock detail; emails keep every detail expanded.
    if not email:
        match = re.search(r'<h2[^>]*>Detailed Analysis</h2>(.*?)(?=<h2|\Z)', body, re.S)
        if match:
            detail = re.sub(r'<h3>(.*?)</h3>(.*?)(?=<h3>|\Z)',
                            r'<details><summary>\1</summary>\2</details>', match[1], flags=re.S)
            body = body[:match.start(1)] + detail + body[match.end(1):]
    body = body.replace('<table>', '<div class="table-scroll"><table>')
    body = body.replace('</table>', '</table></div>')
    navigation = '<nav aria-label="报告目录"><strong>报告导航</strong><ul>' + ''.join(
        f'<li><a href="#{anchor}">{html.escape(title)}</a></li>' for anchor, title in toc) + '</ul></nav>'
    # Place navigation after the introductory metadata and before the first section.
    first = body.find('<h2')
    if first >= 0 and not email:
        body = body[:first] + navigation + body[first:]
    if email:
        # Inline essential styles for email clients that strip the style element.
        for tag, css in [('table','border-collapse:collapse;width:100%;font-size:13px;'),
                         ('th','background:#17364e;color:#fff;padding:10px;text-align:left;'),
                         ('td','padding:10px;border-bottom:1px solid #dce5ef;vertical-align:top;'),
                         ('h2','color:#12324c;margin-top:32px;border-bottom:1px solid #dce5ef;'),
                         ('blockquote','background:#edf8f7;border-left:4px solid #127c80;padding:12px;')]:
            body = re.sub(fr'<{tag}(?=[ >])', f'<{tag} style="{css}"', body)
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Trading Bot · 每日策略报告</title><style>' + STYLE + '</style></head>'
            '<body><main style="font-family:Arial,sans-serif;line-height:1.75;color:#243247;">'
            + body + '</main></body></html>')
