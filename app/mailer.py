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
from html import escape
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


def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """Send a password reset email. Raises on failure."""
    host      = os.environ["SMTP_HOST"]
    port      = int(os.environ.get("SMTP_PORT", "587"))
    user      = os.environ["SMTP_USER"]
    password  = os.environ["SMTP_PASSWORD"]
    from_addr = os.environ.get("SMTP_FROM") or user

    subject = "Reset your Ascent password"

    text_body = f"""\
You requested a password reset for your Ascent account.

Click the link below to set a new password. This link expires in 1 hour and can only be used once:

{reset_url}

If you didn't request this, you can ignore this message — your password won't change.
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
      You requested a password reset for your Ascent account.
    </p>
    <p style="margin-bottom:1.5rem;color:#8e8e93;font-size:13px">
      Click the button below to set a new password. This link expires in 1 hour and can only be used once.
    </p>
    <a href="{reset_url}"
       style="display:inline-block;background:#f97316;color:#fff;text-decoration:none;
              border-radius:8px;padding:.7rem 1.5rem;font-weight:600;font-size:14px">
      Reset Password →
    </a>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      Or copy this link:<br>
      <span style="font-family:monospace;color:#f97316;word-break:break-all">{reset_url}</span>
    </p>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      If you didn't request a password reset, you can ignore this message — your password won't change.
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


# ── Tour share-page followers ────────────────────────────────────────────────
# These mail a list rather than one person, so they share a single SMTP
# connection instead of reconnecting per message like the two senders above.

def _smtp_conf() -> tuple:
    """(host, port, user, password, from_addr) — raises if unconfigured."""
    return (
        os.environ["SMTP_HOST"],
        int(os.environ.get("SMTP_PORT", "587")),
        os.environ["SMTP_USER"],
        os.environ["SMTP_PASSWORD"],
        os.environ.get("SMTP_FROM") or os.environ["SMTP_USER"],
    )


def _smtp_send(messages: list) -> int:
    """Send [(to_addr, msg), ...] over one connection. Returns the number sent.

    A single bad address must not abandon the rest of the batch, so per-message
    failures are counted as unsent and skipped.
    """
    if not messages:
        return 0
    host, port, user, password, from_addr = _smtp_conf()
    context = ssl.create_default_context()
    server = (smtplib.SMTP_SSL(host, port, context=context) if port == 465
              else smtplib.SMTP(host, port))
    sent = 0
    try:
        if port != 465:
            server.ehlo()
            server.starttls(context=context)
        server.login(user, password)
        for to_addr, msg in messages:
            try:
                server.sendmail(from_addr, to_addr, msg.as_string())
                sent += 1
            except Exception:
                pass
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return sent


def _card(inner_html: str) -> str:
    """Wrap body content in the Ascent email card."""
    return f"""\
<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
             background:#1c1c1e;color:#f2f2f7;margin:0;padding:2rem">
  <div style="max-width:480px;margin:0 auto;background:#2c2c2e;
              border:1px solid #3a3a3c;border-radius:16px;padding:2rem">
    <div style="font-size:1.4rem;font-weight:700;color:#f97316;margin-bottom:.5rem">⛰ Ascent</div>
{inner_html}
  </div>
</body>
</html>
"""


def _msg(subject: str, from_addr: str, to_email: str, text_body: str,
         html_body: str, headers: dict = None):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to_email
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    return msg


def send_tour_confirm_email(to_email: str, confirm_url: str, tour_title: str,
                            owner_name: str) -> None:
    """Confirm a share-page subscription. Raises on connection failure."""
    from_addr = _smtp_conf()[4]
    subject = f"Confirm updates for {tour_title}"
    e_title, e_owner = escape(tour_title), escape(owner_name)

    text_body = f"""\
Someone (hopefully you) asked to get an email whenever {owner_name} completes a
stage of "{tour_title}" on Ascent.

Confirm by opening this link:

{confirm_url}

If this wasn't you, ignore this message — no emails will be sent, and your
address will not be kept.
"""

    html_body = _card(f"""\
    <p style="color:#8e8e93;font-size:13px;margin-bottom:1.5rem">Stage updates</p>
    <p style="margin-bottom:1rem">
      Someone (hopefully you) asked to get an email whenever
      <strong>{e_owner}</strong> completes a stage of
      <strong>{e_title}</strong>.
    </p>
    <p style="margin-bottom:1.5rem;color:#8e8e93;font-size:13px">
      Confirm below and you'll hear from us only when a new stage is posted.
    </p>
    <a href="{confirm_url}"
       style="display:inline-block;background:#f97316;color:#fff;text-decoration:none;
              border-radius:8px;padding:.7rem 1.5rem;font-weight:600;font-size:14px">
      Confirm →
    </a>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      Or copy this link:<br>
      <span style="font-family:monospace;color:#f97316;word-break:break-all">{confirm_url}</span>
    </p>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      If this wasn't you, ignore this message — no emails will be sent and your
      address will not be kept.
    </p>""")

    _smtp_send([(to_email, _msg(subject, from_addr, to_email, text_body, html_body))])


def send_stage_update_emails(recipients: list, tour_title: str, owner_name: str,
                             stage_title: str, stats_line: str, summary: str,
                             stage_url: str) -> int:
    """Mail one stage announcement to each follower.

    `recipients` is [(email, unsubscribe_url)] — one message per person so each
    carries its own unsubscribe link. Returns the number actually sent.
    """
    from_addr = _smtp_conf()[4]
    subject = f"{tour_title}: {stage_title}"
    e_title, e_owner = escape(tour_title), escape(owner_name)
    e_stage, e_stats = escape(stage_title), escape(stats_line)

    summary_text = f"\n{summary}\n" if summary else ""
    summary_html = (
        f'<p style="margin-bottom:1.5rem;color:#c7c7cc;font-size:13px;line-height:1.55">'
        f'{escape(summary)}</p>' if summary else "")

    messages = []
    for email, unsub_url in recipients:
        text_body = f"""\
{owner_name} just completed a stage of "{tour_title}".

{stage_title}
{stats_line}
{summary_text}
See the route, the map and the ride data:

{stage_url}

---
Stop receiving these: {unsub_url}
"""
        html_body = _card(f"""\
    <p style="color:#8e8e93;font-size:13px;margin-bottom:1.5rem">
      <strong>{e_owner}</strong> just completed a stage of {e_title}
    </p>
    <div style="font-size:1.05rem;font-weight:600;margin-bottom:.35rem">{e_stage}</div>
    <div style="color:#8e8e93;font-size:13px;margin-bottom:1.25rem">{e_stats}</div>
{summary_html}
    <a href="{stage_url}"
       style="display:inline-block;background:#f97316;color:#fff;text-decoration:none;
              border-radius:8px;padding:.7rem 1.5rem;font-weight:600;font-size:14px">
      View the stage →
    </a>
    <p style="margin-top:1.5rem;font-size:11px;color:#636366">
      You're getting this because you asked for updates on this tour.
      <a href="{unsub_url}" style="color:#636366">Unsubscribe</a>.
    </p>""")

        messages.append((email, _msg(
            subject, from_addr, email, text_body, html_body,
            # Lets Gmail and Apple Mail show their own one-tap unsubscribe.
            {"List-Unsubscribe": f"<{unsub_url}>"},
        )))

    return _smtp_send(messages)
