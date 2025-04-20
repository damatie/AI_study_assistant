import httpx
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
    Send an email via Resend API.
    """
    payload = {
        "from": settings.RESEND_FROM_EMAIL,
        "to": recipient,
        "subject": subject,
    }

    # Add content based on what's provided
    if html_content:
        payload["html"] = html_content

    if text_content:
        payload["text"] = text_content
    elif body:
        payload["text"] = body

    async with httpx.AsyncClient() as client:
        headers = {
            "Authorization": f"Bearer {settings.RESEND_API_KEY}",
            "Content-Type": "application/json",
        }

        response = await client.post(
            "https://api.resend.com/emails", json=payload, headers=headers
        )

        if response.status_code >= 400:
            error_data = response.json()
            raise Exception(
                f"Failed to send email: {error_data.get('message', 'Unknown error')}"
            )


async def send_verification_email(email: str, code: str):
    tpl = env.get_template("verification.html")
    html = tpl.render(
        code=code,
        app_name="AI Study Assistant",
        support_email=settings.RESEND_FROM_EMAIL,
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
        code=code,
        app_name="AI Study Assistant",
        support_email=settings.RESEND_FROM_EMAIL,
    )
    text = f"Your password reset code is {code}. It expires in 10 minutes."
    await send_email(
        subject="Reset your AI Study Assistant password",
        recipient=email,
        html_content=html,
        text_content=text,
    )
