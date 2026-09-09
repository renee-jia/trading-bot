"""
Email sender for daily stock recommendation reports.

Sends HTML-formatted reports via Gmail SMTP using app password credentials
stored in .env file.
"""
import smtplib
import os
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime
from pathlib import Path

# Load .env manually (no dotenv dependency needed)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def send_report_email(report_content, subject=None, attachments=None, text_summary=None):
    """
    Send the report: a decision-first HTML digest in the body (kept under
    Gmail's ~102KB clip limit) plus the full report as attachments.

    Args:
        report_content: The markdown report string.
        subject: Optional custom subject line.
        attachments: Optional list of file paths (e.g. the .html and .md report).
        text_summary: Optional plain-text digest for text-only clients; the
            full Markdown is used when omitted.

    Returns:
        True if sent successfully, False otherwise.
    """
    sender = os.environ.get("REPORT_EMAIL_FROM")
    password = os.environ.get("REPORT_EMAIL_PASSWORD")
    recipients_str = os.environ.get("REPORT_EMAIL_TO")

    if not all([sender, password, recipients_str]):
        print("Error: Missing email credentials in .env file.")
        return False

    # Support comma-separated recipient list
    recipients = [r.strip() for r in recipients_str.split(",") if r.strip()]

    if subject is None:
        subject = f"Daily Stock Report - {datetime.now().strftime('%Y-%m-%d')}"

    msg = MIMEMultipart("mixed")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    html_body, omitted = _markdown_to_html(report_content)
    plain = text_summary or report_content
    if attachments:
        plain += "\n\n完整报告见附件：" + ", ".join(os.path.basename(p) for p in attachments)
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain, "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alternative)
    for path in attachments or []:
        try:
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(path))
            msg.attach(part)
        except OSError as e:
            print(f"Skipping attachment {path}: {e}")
    if omitted:
        print(f"Email body omits {len(omitted)} section(s); full report attached.")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
        print(f"Report emailed to {', '.join(recipients)}")
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


def _markdown_to_html(md):
    """Same typography and safe Markdown as the saved report, sized for Gmail."""
    from report_format import email_digest
    return email_digest(md)
