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
    """Basic markdown-to-HTML conversion for email rendering."""
    lines = md.split("\n")
    html_lines = []
    in_table = False
    is_header_row = True

    html_lines.append("""<html><body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">""")

    for line in lines:
        stripped = line.strip()

        # Horizontal rules
        if stripped == "---":
            if in_table:
                html_lines.append("</table>")
                in_table = False
            html_lines.append("<hr>")
            continue

        # Table separator row (|---|---|)
        if stripped.startswith("|") and all(c in "-| " for c in stripped):
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            if not in_table:
                html_lines.append('<table border="1" cellpadding="6" cellspacing="0" '
                                  'style="border-collapse: collapse; margin: 10px 0;">')
                in_table = True
                is_header_row = True

            cells = [c.strip() for c in stripped.strip("|").split("|")]
            tag = "th" if is_header_row else "td"
            style = ' style="background: #f0f0f0; font-weight: bold;"' if is_header_row else ""
            row = "".join(f"<{tag}{style}>{_bold(c)}</{tag}>" for c in cells)
            html_lines.append(f"<tr>{row}</tr>")
            is_header_row = False
            continue

        if in_table:
            html_lines.append("</table>")
            in_table = False

        # Headers
        if stripped.startswith("# "):
            html_lines.append(f"<h1>{stripped[2:]}</h1>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h2>{stripped[3:]}</h2>")
        elif stripped.startswith("### "):
            html_lines.append(f"<h3>{stripped[4:]}</h3>")
        elif stripped.startswith("- "):
            html_lines.append(f"<li>{_bold(stripped[2:])}</li>")
        elif stripped.startswith("**") and stripped.endswith("**"):
            html_lines.append(f"<p><strong>{stripped[2:-2]}</strong></p>")
        elif stripped:
            html_lines.append(f"<p>{_bold(stripped)}</p>")

    if in_table:
        html_lines.append("</table>")

    html_lines.append("</body></html>")
    return "\n".join(html_lines)


def _bold(text):
    """Replace **text** with <strong>text</strong>."""
    import re
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
