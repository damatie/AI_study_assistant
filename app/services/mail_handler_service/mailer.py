import aiosmtplib
from email.message import EmailMessage
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings

# set up Jinja2 to load from app/services/templates/
env = Environment(
    loader=FileSystemLoader("app/services/mail_handler/templates"),
    autoescape=select_autoescape(["html", "xml"]),
)


async def send_email(
    subject: str,
    recipient: str,
    body: str = None,
    html_content: str = None,
    text_content: str = None,
) -> None:
    """
    Send an email via your SMTP server with proper Unicode handling.
    """
    # Use MIMEMultipart for better encoding support
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.FROM_EMAIL
    msg["To"] = recipient
    msg["Subject"] = subject

    # Add text part first (as fallback)
    if text_content:
        msg.attach(MIMEText(text_content, "plain", "utf-8"))
    elif body:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    # Add HTML part if provided
    if html_content:
        msg.attach(MIMEText(html_content, "html", "utf-8"))

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_SERVER,
        port=settings.SMTP_PORT,
        username=settings.EMAIL_USERNAME,
        password=settings.EMAIL_PASSWORD,
        start_tls=True,
    )


async def send_verification_email(email: str, code: str):
    tpl = env.get_template("verification.html")
    html = tpl.render(
        code=code, app_name="AI Study Assistant", support_email=settings.FROM_EMAIL
    )
    text = f"Your verification code is {code}. It expires in 10 minutes."
    await send_email(
        subject="Verify your AI Study Assistant account",
        recipient=email,
        html_content=html,
        text_content=text,
    )


async def send_reset_password_email(email: str, code: str):
    tpl = env.get_template("reset_password.html")
    html = tpl.render(
        code=code, app_name="AI Study Assistant", support_email=settings.FROM_EMAIL
    )
    text = f"Your password reset code is {code}. It expires in 10 minutes."
    await send_email(
        subject="Reset your AI Study Assistant password",
        recipient=email,
        html_content=html,
        text_content=text,
    )
