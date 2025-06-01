import os
from typing import Optional, List, Union, Dict, Any
from jinja2 import Environment, FileSystemLoader, select_autoescape
import resend
from app.core.config import settings

# set up Jinja2 to load from app/services/templates/
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

# Initialize Resend with API key
resend.api_key = settings.RESEND_API_KEY


class EmailError(Exception):
    """Custom exception for email sending errors"""
    pass


async def send_email(
    subject: str,
    recipient: Union[str, List[str]],
    body: Optional[str] = None,
    html_content: Optional[str] = None,
    text_content: Optional[str] = None,
    sender: Optional[str] = None,
    reply_to: Optional[str] = None,
    cc: Optional[Union[str, List[str]]] = None,
    bcc: Optional[Union[str, List[str]]] = None,
    headers: Optional[Dict[str, str]] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Send an email via Resend API using the official SDK.
    
    Args:
        subject: Email subject line
        recipient: Single email or list of emails (max 50)
        body: Plain text body (will be used as text_content if no text_content provided)
        html_content: HTML version of the email
        text_content: Plain text version of the email
        sender: Sender email (defaults to settings.RESEND_FROM_EMAIL)
        reply_to: Reply-to email address
        cc: CC recipients
        bcc: BCC recipients
        headers: Custom headers dictionary
        tags: Custom tags for tracking
    
    Returns:
        Dict containing email ID and other response data
        
    Raises:
        EmailError: If email sending fails
    """
    
    # Prepare the email parameters
    params: resend.Emails.SendParams = {
        "from": sender or settings.RESEND_FROM_EMAIL,
        "to": [recipient] if isinstance(recipient, str) else recipient,
        "subject": subject,
    }
    
    # Add content based on what's provided
    if html_content:
        params["html"] = html_content
        
    if text_content:
        params["text"] = text_content
    elif body:
        params["text"] = body
        
    # Add optional parameters
    if reply_to:
        params["reply_to"] = [reply_to] if isinstance(reply_to, str) else reply_to
        
    if cc:
        params["cc"] = [cc] if isinstance(cc, str) else cc
        
    if bcc:
        params["bcc"] = [bcc] if isinstance(bcc, str) else bcc
        
    if headers:
        params["headers"] = headers
        
    if tags:
        params["tags"] = tags
    
    try:
        # Send email using the official Resend SDK
        response = resend.Emails.send(params)
        
        # The SDK returns the response directly, but let's ensure it's valid
        if not response or not response.get("id"):
            raise EmailError("Invalid response from Resend API - no email ID returned")
            
        return response
        
    except Exception as e:
        # Handle various types of errors
        if hasattr(e, 'status_code'):
            # HTTP error from Resend API
            raise EmailError(f"Resend API error ({e.status_code}): {str(e)}")
        else:
            # Other errors (network, validation, etc.)
            raise EmailError(f"Failed to send email: {str(e)}")


async def send_verification_email(email: str, code: str, name:str) -> Dict[str, Any]:
    """
    Send a verification email with the provided code.
    
    Args:
        email: Recipient email address
        code: Verification code to include in the email
        
    Returns:
        Dict containing email ID and response data
    """
    try:
        # Render HTML template
        tpl = env.get_template("verification.html")
        html = tpl.render(
            code=code,
            name=name,
            app_name="knoledg",
            support_email=settings.RESEND_FROM_EMAIL,
             logo_url=settings.LOGO
        )
        
        # Plain text fallback
        text = f"""
knoledg - Email Verification

Your verification code is: {code}

This code expires in 10 minutes. If you didn't request this verification, please ignore this email.

Need help? Contact us at {settings.RESEND_FROM_EMAIL}
        """.strip()
        
        return await send_email(
            subject="Verify your knoledg account",
            recipient=email,
            html_content=html,
            text_content=text,
            tags={"type": "verification", "app": "ai-study-assistant"}
        )
        
    except Exception as e:
        raise EmailError(f"Failed to send verification email to {email}: {str(e)}")


async def send_reset_password_email(email: str, code: str, name: str) -> Dict[str, Any]:
    """
    Send a password reset email with the provided code.
    
    Args:
        email: Recipient email address
        code: Password reset code to include in the email
        
    Returns:
        Dict containing email ID and response data
    """
    try:
        # Render HTML template
        tpl = env.get_template("reset_password.html")
        html = tpl.render(
            code=code,
            name=name,
            app_name="knoledg",
            support_email=settings.RESEND_FROM_EMAIL, 
            logo_url=settings.LOGO
        )
        
        # Plain text fallback
        text = f"""
knoledg - Password Reset

Your password reset code is: {code}

This code expires in 10 minutes. If you didn't request a password reset, please ignore this email and your password will remain unchanged.

Need help? Contact us at {settings.RESEND_FROM_EMAIL}
        """.strip()
        
        return await send_email(
            subject="Reset your knoledg password",
            recipient=email,
            html_content=html,
            text_content=text,
            tags={"type": "password-reset", "app": "ai-study-assistant"}
        )
        
    except Exception as e:
        raise EmailError(f"Failed to send password reset email to {email}: {str(e)}")


# Additional utility functions

async def send_welcome_email(email: str, name: str) -> Dict[str, Any]:
    """Send a welcome email to new users"""
    try:
        tpl = env.get_template("welcome.html")
        html = tpl.render(
            name=name,
            app_name="knoledg",
            support_email=settings.RESEND_FROM_EMAIL,
             logo_url=settings.LOGO
        )
        
        text = f"""
Welcome to knoledg, {name}!

Thank you for signing up. We're excited to help you with your studies.

Get started by logging into your account and exploring our features.

Need help? Contact us at {settings.RESEND_FROM_EMAIL}
        """.strip()
        
        return await send_email(
            subject="Welcome to knoledg!",
            recipient=email,
            html_content=html,
            text_content=text,
            tags={"type": "welcome", "app": "ai-study-assistant"}
        )
        
    except Exception as e:
        raise EmailError(f"Failed to send welcome email to {email}: {str(e)}")


async def send_notification_email(
    email: str,
    subject: str,
    message: str,
    html_message: Optional[str] = None
) -> Dict[str, Any]:
    """Send a general notification email"""
    try:
        return await send_email(
            subject=f"knoledg - {subject}",
            recipient=email,
            html_content=html_message,
            text_content=message,
            tags={"type": "notification", "app": "ai-study-assistant"}
        )
        
    except Exception as e:
        raise EmailError(f"Failed to send notification email to {email}: {str(e)}")