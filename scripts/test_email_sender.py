"""Email composition regressions: digest body under the Gmail clip, full report attached. No network."""
import email
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import email_sender
from report_format import EMAIL_BUDGET


class _Server:
    sent = []
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def login(self, *a): pass
    def sendmail(self, sender, recipients, raw): _Server.sent.append((sender, recipients, raw))


def test_digest_body_attachments_and_plain_summary(tmp_path):
    table = '| 列 | 值 |\n|---|---|\n' + ''.join(f'| 行{i} | {"数据" * 20} |\n' for i in range(80))
    md = '# R\n\n' + ''.join(f'## Section {n}\n\n开头。\n\n{table}\n---\n' for n in range(30))
    md += '## Detailed Analysis\n\n### AAPL - Apple\n\n' + table
    report = tmp_path / 'recommendations_x.md'
    report.write_text(md)
    html = tmp_path / 'recommendations_x.html'
    html.write_text('<html>full</html>')
    env = {'REPORT_EMAIL_FROM': 'a@example.com', 'REPORT_EMAIL_PASSWORD': 'p', 'REPORT_EMAIL_TO': 'b@example.com, c@example.com'}
    _Server.sent.clear()
    with patch.dict(os.environ, env), patch.object(email_sender.smtplib, 'SMTP_SSL', _Server):
        ok = email_sender.send_report_email(md, subject='S', attachments=[str(html), str(report)],
                                            text_summary='SUMMARY LINE')
    assert ok and len(_Server.sent) == 1
    sender, recipients, raw = _Server.sent[0]
    assert recipients == ['b@example.com', 'c@example.com']
    msg = email.message_from_string(raw)
    assert msg.get_content_type() == 'multipart/mixed'
    parts = list(msg.walk())
    alternative = next(p for p in parts if p.get_content_type() == 'multipart/alternative')
    plain, body_html = [p for p in alternative.get_payload()]
    assert plain.get_content_type() == 'text/plain' and 'SUMMARY LINE' in plain.get_payload(decode=True).decode()
    assert '完整报告见附件' in plain.get_payload(decode=True).decode()
    html_bytes = body_html.get_payload(decode=True)
    assert body_html.get_content_type() == 'text/html' and len(html_bytes) <= EMAIL_BUDGET
    assert 'Section 0' in html_bytes.decode() and '邮件正文说明' in html_bytes.decode()
    attachments = [p.get_filename() for p in parts if p.get('Content-Disposition', '').startswith('attachment')]
    assert attachments == ['recommendations_x.html', 'recommendations_x.md']


def test_missing_credentials_is_reported_not_raised():
    with patch.dict(os.environ, {'REPORT_EMAIL_FROM': '', 'REPORT_EMAIL_PASSWORD': '', 'REPORT_EMAIL_TO': ''}):
        assert email_sender.send_report_email('# x') is False
