"""
app/mailer.py — Simple SMTP email sender for invite links.

Required env vars:
  SMTP_HOST      e.g. smtp.gmail.com
  SMTP_PORT      e.g. 587 (STARTTLS) or 465 (SSL)
  SMTP_USER      your SMTP login username
  SMTP_PASSWORD  your SMTP login password
  SMTP_FROM      address shown in From: header (defaults to SMTP_USER)
"""

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def send_invite_email(to_email: str, invite_url: str, invited_by: str = "Someone") -> None:
    """Send an invite email. Raises on failure."""
    host     = os.environ["SMTP_HOST"]
    port     = int(os.environ.get("SMTP_PORT", "587"))
    user     = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    from_addr = os.environ.get("SMTP_FROM") or user

    subject = f"{invited_by} invited you to Ascent"

    text_body = f"""\
You've been invited to join Ascent, a personal fitness activity tracker.

Click the link below to create your account — it can only be used once:

{invite_url}

If you weren't expecting this, you can ignore this message.
"""

    html_body = f"""\
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#1c1c1e;color:#f2f2f7;margin:0;padding:2rem">
  <div style="max-width:480px;margin:0 auto;background:#2c2c2e;
              border:1px solid #3a3a3c;border-radius:16px;padding:2rem">
    <div style="font-size:1.4rem;font-weight:700;color:#f97316;margin-bottom:.5rem">⛰ Ascent</div>
    <p style="color:#8e8e93;font-size:13px;margin-bottom:1.5rem">Personal fitness activity tracker</p>
    <p style="margin-bottom:1rem">
      <strong>{invited_by}</strong> has invited you to join Ascent.
    </p>
    <p style="margin-bottom:1.5rem;color:#8e8e93;font-size:13px">
      Click the button below to create your account. This link can only be used once.
    </p>
    <a href="{invite_url}"
       style="display:inline-block;background:#f97316;color:#fff;text-decoration:none;
              border-radius:8px;padding:.7rem 1.5rem;font-weight:600;font-size:14px">
      Accept Invite →
    </a>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      Or copy this link:<br>
      <span style="font-family:monospace;color:#f97316;word-break:break-all">{invite_url}</span>
    </p>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      If you weren't expecting this invitation, you can ignore this message.
    </p>
  </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            server.login(user, password)
            server.sendmail(from_addr, to_email, msg.as_string())
    else:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(user, password)
            server.sendmail(from_addr, to_email, msg.as_string())
