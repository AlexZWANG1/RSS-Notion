"""SMTP email sender for AI Daily Digest reports."""

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)


def send_report_email(
    attachment_path: str, executive_summary: str, report_date: str
) -> bool:
    """Send the daily digest report via SMTP email.

    Args:
        attachment_path: Path to the report file (PDF or image) to attach.
        executive_summary: Plain-text executive summary used as the email body.
        report_date: Date string (e.g. "2026-03-19") used in the subject line.

    Returns:
        True on success, False on failure.
    """
    # --------------- read config from environment ---------------
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    recipients_raw = os.getenv("EMAIL_RECIPIENTS")
    email_from = os.getenv("EMAIL_FROM")

    # Validate required config
    missing = []
    if not smtp_host:
        missing.append("SMTP_HOST")
    if not smtp_user:
        missing.append("SMTP_USER")
    if not smtp_password:
        missing.append("SMTP_PASSWORD")
    if not recipients_raw:
        missing.append("EMAIL_RECIPIENTS")
    if not email_from:
        missing.append("EMAIL_FROM")

    if missing:
        logger.warning(
            "Email not sent — missing required env vars: %s",
            ", ".join(missing),
        )
        return False

    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    if not recipients:
        logger.warning("EMAIL_RECIPIENTS is set but contains no valid addresses")
        return False

    # --------------- build the message ---------------
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"[AI日报] {report_date} 每日认知日报"
    msg["From"] = email_from
    msg["To"] = ", ".join(recipients)

    # Plain-text body with executive summary
    msg.attach(MIMEText(executive_summary, "plain", "utf-8"))

    # Attachment (auto-detect type by extension)
    att_file = Path(attachment_path)
    if not att_file.exists():
        logger.warning("Attachment file not found: %s", attachment_path)
        return False

    suffix = att_file.suffix.lower()
    with open(att_file, "rb") as f:
        data = f.read()

    if suffix == ".pdf":
        part = MIMEApplication(data, _subtype="pdf")
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        subtype = "png" if suffix == ".png" else "jpeg" if suffix in (".jpg", ".jpeg") else "webp"
        part = MIMEImage(data, _subtype=subtype)
    else:
        part = MIMEApplication(data)

    part.add_header(
        "Content-Disposition", "attachment", filename=att_file.name
    )
    msg.attach(part)

    # --------------- send ---------------
    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(email_from, recipients, msg.as_string())
        logger.info(
            "Report email sent to %s for date %s",
            ", ".join(recipients),
            report_date,
        )
        return True
    except Exception:
        logger.exception("Failed to send report email")
        return False
