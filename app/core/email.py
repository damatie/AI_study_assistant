# app/core/email.py
from email.message import EmailMessage
import aiosmtplib
from app.core.config import settings


async def send_email(
    subject: str,
    recipient: str,
    body: str,
    html: bool = False,
) -> None:
    """
    Send an email via your SMTP server.
    """
    msg = EmailMessage()
    msg["From"] = settings.FROM_EMAIL
    msg["To"] = recipient
    msg["Subject"] = subject
    if html:
        msg.add_header("Content-Type", "text/html")
        msg.set_payload(body)
    else:
        msg.set_content(body)

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_SERVER,
        port=settings.SMTP_PORT,
        username=settings.EMAIL_USERNAME,
        password=settings.EMAIL_PASSWORD,
        start_tls=True,
    )
