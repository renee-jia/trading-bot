"""
Email sender for daily stock recommendation reports.

Sends HTML-formatted reports via Gmail SMTP using app password credentials
stored in .env file.
"""
import smtplib
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
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


def send_report_email(report_content, subject=None):
    """
    Send the markdown report as an email.

    Args:
        report_content: The markdown report string.
        subject: Optional custom subject line.

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

    msg = MIMEMultipart("alternative")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    # Convert markdown tables to simple HTML for better email rendering
    html_body = _markdown_to_html(report_content)
    msg.attach(MIMEText(report_content, "plain"))
    msg.attach(MIMEText(html_body, "html"))

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
    """Use the same typography, tables and safe Markdown as the saved report."""
    from report_format import render_html
    return render_html(md, email=True)
