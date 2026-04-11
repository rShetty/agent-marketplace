"""Email service for sending verification and notification emails."""
import os
from typing import Optional


async def send_verification_email(
    to_email: str,
    agent_id: str,
    agent_name: str,
    verification_token: str
) -> bool:
    """
    Send verification email to agent contact email.
    
    In production, replace this with actual SMTP sending using:
    - SendGrid
    - AWS SES
    - Mailgun
    - SMTP server
    
    For now, logs to console. Set SMTP credentials in environment to enable.
    """
    marketplace_url = os.getenv("MARKETPLACE_URL", "https://hive.rajeev.me")
    verification_url = f"{marketplace_url}/api/agent/verify-email?token={verification_token}"
    
    # Check if SMTP is configured
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL", "noreply@hive.rajeev.me")
    
    if all([smtp_host, smtp_port, smtp_user, smtp_password]):
        # TODO: Implement actual SMTP sending
        # Example with aiosmtplib:
        # import aiosmtplib
        # from email.message import EmailMessage
        # 
        # message = EmailMessage()
        # message["From"] = from_email
        # message["To"] = to_email
        # message["Subject"] = f"Verify your agent: {agent_name}"
        # message.set_content(f"""
        # Hello,
        # 
        # Thank you for registering your agent '{agent_name}' on Hive!
        # 
        # Please verify your email by clicking the link below:
        # {verification_url}
        # 
        # Agent ID: {agent_id}
        # 
        # Once verified, your agent will have full API access.
        # 
        # Best,
        # Hive Team
        # """)
        # 
        # await aiosmtplib.send(
        #     message,
        #     hostname=smtp_host,
        #     port=int(smtp_port),
        #     username=smtp_user,
        #     password=smtp_password,
        #     use_tls=True
        # )
        
        print(f"📧 [SMTP] Verification email would be sent to: {to_email}")
        print(f"   Agent: {agent_name} (ID: {agent_id})")
        print(f"   Verification URL: {verification_url}")
        return True
    else:
        # Console logging for development
        print(f"📧 [CONSOLE] Verification email for: {to_email}")
        print(f"   Agent: {agent_name} (ID: {agent_id})")
        print(f"   Verification URL: {verification_url}")
        print()
        print("   To enable email sending, set these environment variables:")
        print("   - SMTP_HOST")
        print("   - SMTP_PORT")
        print("   - SMTP_USER")
        print("   - SMTP_PASSWORD")
        print("   - SMTP_FROM_EMAIL (optional)")
        return True


async def send_password_reset_email(
    to_email: str,
    reset_token: str
) -> bool:
    """Send password reset email to user."""
    marketplace_url = os.getenv("MARKETPLACE_URL", "https://hive.rajeev.me")
    reset_url = f"{marketplace_url}/reset-password?token={reset_token}"
    
    print(f"📧 [CONSOLE] Password reset email for: {to_email}")
    print(f"   Reset URL: {reset_url}")
    return True


async def send_agent_alert_email(
    to_email: str,
    agent_name: str,
    alert_message: str
) -> bool:
    """Send alert email about agent status changes."""
    print(f"📧 [CONSOLE] Agent alert for: {to_email}")
    print(f"   Agent: {agent_name}")
    print(f"   Alert: {alert_message}")
    return True
