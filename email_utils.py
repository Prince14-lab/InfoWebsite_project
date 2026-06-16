import os
import smtplib
from email.message import EmailMessage

from flask import current_app


def _smtp_config():
    return {
        "host": current_app.config.get("SMTP_HOST") or os.getenv("SMTP_HOST") or "smtp.gmail.com",
        "port": int(current_app.config.get("SMTP_PORT") or os.getenv("SMTP_PORT") or "587"),
        "user": current_app.config.get("SMTP_USER") or os.getenv("SMTP_USER"),
        "password": current_app.config.get("SMTP_PASSWORD") or os.getenv("SMTP_PASSWORD"),
        "from_email": (
            current_app.config.get("SMTP_FROM_EMAIL")
            or os.getenv("SMTP_FROM_EMAIL")
            or current_app.config.get("SMTP_USER")
            or os.getenv("SMTP_USER")
        ),
    }


def send_email(to_email, subject, body, reply_to=None):
    """Send a plain text email. Returns False instead of breaking the main request."""
    if not to_email:
        current_app.logger.warning("Email not sent: missing recipient.")
        return False

    config = _smtp_config()
    if not config["user"] or not config["password"]:
        current_app.logger.warning("Email not sent: SMTP_USER or SMTP_PASSWORD is not configured.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["from_email"] or config["user"]
    message["To"] = to_email
    if reply_to:
        message["Reply-To"] = reply_to
    message.set_content(body)

    try:
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as smtp:
            smtp.starttls()
            smtp.login(config["user"], config["password"])
            smtp.send_message(message)
        return True
    except Exception as error:
        current_app.logger.exception("Email failed: %s", error)
        return False


def safe_send_email(to_email, subject, body, reply_to=None):
    return send_email(to_email, subject, body, reply_to=reply_to)
