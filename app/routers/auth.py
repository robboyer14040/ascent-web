"""
routers/auth.py — Login, logout, register, and invite routes.
"""

import os
import time
from typing import Callable, Optional

from fastapi import APIRouter, Request, Form, Query, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()
db_getter: Callable = None
templates = None

# ── Login ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = Query("/"), error: str = Query(None)):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "next":    next,
        "error":   error,
    })

@router.post("/login")
async def login_submit(
    request: Request,
    email:    str = Form(...),
    password: str = Form(...),
    next:     str = Form("/"),
):
    from app.auth import verify_password, set_session_cookie

    db      = db_getter()
    login   = email.strip()
    user    = db.get_user_by_email(login.lower()) or db.get_user_by_username(login)

    if not user or not user.get("password_hash"):
        return RedirectResponse(f"/login?next={next}&error=Invalid+username/email+or+password", status_code=303)

    if not verify_password(password, user["password_hash"]):
        return RedirectResponse(f"/login?next={next}&error=Invalid+username/email+or+password", status_code=303)

    response = RedirectResponse(next or "/", status_code=303)
    set_session_cookie(response, user["id"])
    return response

# ── Logout ────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout():
    from app.auth import clear_session_cookie
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response

# ── Register (invite-only) ────────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, token: str = Query(...), error: str = Query(None)):
    db     = db_getter()
    invite = db.get_invite(token)
    if not invite or invite.get("used_at"):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "This invite link is invalid or has already been used.",
        })
    return templates.TemplateResponse("register.html", {
        "request": request,
        "token":   token,
        "email":   invite.get("email", ""),
        "error":   error,
    })

@router.post("/register")
async def register_submit(
    request:  Request,
    token:    str = Form(...),
    username: str = Form(...),
    email:    str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
):
    from app.auth import hash_password, set_session_cookie

    db     = db_getter()
    invite = db.get_invite(token)
    if not invite or invite.get("used_at"):
        return RedirectResponse("/register?token=invalid&error=Invalid+invite", status_code=303)

    if password != password2:
        return RedirectResponse(f"/register?token={token}&error=Passwords+do+not+match", status_code=303)

    if len(password) < 8:
        return RedirectResponse(f"/register?token={token}&error=Password+must+be+at+least+8+characters", status_code=303)

    username = username.strip()
    if not username:
        return RedirectResponse(f"/register?token={token}&error=Username+is+required", status_code=303)
    if db.get_user_by_username(username):
        return RedirectResponse(f"/register?token={token}&error=Username+already+taken,+please+choose+another", status_code=303)

    email = email.strip().lower()
    if db.get_user_by_email(email):
        return RedirectResponse(f"/register?token={token}&error=Email+already+registered", status_code=303)

    user_id = db.create_user(
        email=email,
        username=username,
        password_hash=hash_password(password),
        invited_by=invite.get("invited_by_user_id"),
    )
    db.mark_invite_used(token, user_id)

    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user_id)
    return response

# ── Strava OAuth login ────────────────────────────────────────────────────────
# When Strava callback completes, if a user with that athlete_id exists → log them in.
# If not, show a "connect to existing account" or "no invite" message.

@router.get("/auth/strava/callback", response_class=HTMLResponse)
async def strava_auth_callback(
    request: Request,
    code:    str = Query(None),
    error:   str = Query(None),
):
    """
    Strava OAuth callback for LOGIN (not sync).
    Separate from /strava/callback which is for syncing activities.
    """
    import httpx
    from app.auth import set_session_cookie
    from app.routers.strava import STRAVA_TOKEN_URL

    if error or not code:
        return RedirectResponse(f"/login?error=Strava+auth+failed", status_code=303)

    callback_uri = str(request.base_url).rstrip("/") + "/auth/strava/callback"
    override = os.environ.get("STRAVA_AUTH_REDIRECT_URI")
    if override:
        callback_uri = override

    async with httpx.AsyncClient() as client:
        resp = await client.post(STRAVA_TOKEN_URL, data={
            "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
            "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  callback_uri,
        })

    if resp.status_code != 200:
        return RedirectResponse("/login?error=Strava+auth+failed", status_code=303)

    data        = resp.json()
    athlete     = data.get("athlete", {})
    athlete_id  = str(athlete.get("id", ""))
    access_tok  = data["access_token"]
    refresh_tok = data["refresh_token"]
    expires_at  = data["expires_at"]

    db   = db_getter()
    user = db.get_user_by_strava_athlete_id(athlete_id)

    if user:
        # Existing user — update their tokens and log in
        db.update_user_strava_tokens(user["id"], {
            "access_token":  access_tok,
            "refresh_token": refresh_tok,
            "expires_at":    expires_at,
            "athlete":       athlete,
        })
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, user["id"])
        return response
    else:
        # No account linked to this Strava athlete
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": (
                f"No Ascent account is linked to your Strava profile "
                f"({athlete.get('firstname', '')} {athlete.get('lastname', '')}).\n"
                "Ask the admin for an invite link to create an account."
            ),
        })

@router.get("/auth/strava")
async def strava_auth_start(request: Request):
    """Redirect to Strava OAuth for login purposes."""
    from app.routers.strava import STRAVA_AUTH_URL, STRAVA_SCOPE
    client_id    = os.environ.get("STRAVA_CLIENT_ID", "")
    callback_uri = str(request.base_url).rstrip("/") + "/auth/strava/callback"
    override     = os.environ.get("STRAVA_AUTH_REDIRECT_URI")
    if override:
        callback_uri = override
    url = (f"{STRAVA_AUTH_URL}?client_id={client_id}&response_type=code"
           f"&redirect_uri={callback_uri}&approval_prompt=auto&scope={STRAVA_SCOPE}")
    return RedirectResponse(url)

# ── Admin: invite management ──────────────────────────────────────────────────

@router.get("/admin/invites", response_class=HTMLResponse)
async def admin_invites(request: Request):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        return RedirectResponse("/login?next=/admin/invites", status_code=303)

    db   = db_getter()
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        return templates.TemplateResponse("error.html", {
            "request": request,
            "message": "Admin access required.",
        })

    import getpass
    invites = db.list_invites()
    users   = db.list_users()
    try:
        db_path        = db.path
        activity_count = db.count_activities()
    except Exception:
        db_path        = "Not connected"
        activity_count = 0
    return templates.TemplateResponse("admin_invites.html", {
        "request":        request,
        "invites":        invites,
        "users":          users,
        "current_user":   user,
        "db_path":        db_path,
        "activity_count": activity_count,
        "username":       getpass.getuser(),
    })

@router.post("/admin/invites/create")
async def admin_create_invite(
    request: Request,
    email:   str = Form(""),
):
    from app.auth import get_session_user_id, generate_invite_token
    uid = get_session_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)

    db   = db_getter()
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)

    token = generate_invite_token()
    clean_email = email.strip().lower()
    db.create_invite(email=clean_email, invited_by_user_id=uid, token=token)

    base = str(request.base_url).rstrip("/")
    invite_url = f"{base}/register?token={token}"

    email_status = None  # None = not attempted
    if clean_email:
        from app.mailer import smtp_configured, send_invite_email
        if smtp_configured():
            try:
                send_invite_email(
                    to_email=clean_email,
                    invite_url=invite_url,
                    invited_by=user.get("username") or "Your Ascent admin",
                )
                email_status = "sent"
            except Exception as exc:
                email_status = f"failed: {exc}"
        else:
            email_status = "not_configured"

    import getpass
    try:
        db_path        = db.path
        activity_count = db.count_activities()
    except Exception:
        db_path        = "Not connected"
        activity_count = 0
    return templates.TemplateResponse("admin_invites.html", {
        "request":        request,
        "invites":        db.list_invites(),
        "users":          db.list_users(),
        "current_user":   user,
        "new_invite_url": invite_url,
        "invite_email":   clean_email,
        "email_status":   email_status,
        "db_path":        db_path,
        "activity_count": activity_count,
        "username":       getpass.getuser(),
    })

@router.post("/admin/users/delete")
async def admin_delete_user(
    request: Request,
    user_id: int = Form(...),
):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    db   = db_getter()
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    # Prevent deleting yourself or other admins
    target = db.get_user(user_id)
    if not target or target.get("is_admin") or user_id == uid:
        return RedirectResponse("/admin/invites", status_code=303)

    # Collect Strava IDs before deletion so we can clean photos on disk
    try:
        strava_ids = [
            r[0] for r in db._con.execute(
                "SELECT strava_id FROM activities WHERE user_id=? AND strava_id IS NOT NULL",
                (user_id,)
            ).fetchall()
        ]
    except Exception:
        strava_ids = []

    # Delete all user data from DB
    db.delete_user(user_id)

    # Remove photos on disk for this user's activities (best-effort)
    try:
        import os, shutil
        from pathlib import Path
        db_path = os.environ.get("ASCENT_DB_PATH", "")
        if db_path and strava_ids:
            photos_root = Path(db_path).parent / "support" / "photos"
            for sid in strava_ids:
                photo_dir = photos_root / str(sid)
                if photo_dir.exists():
                    shutil.rmtree(photo_dir, ignore_errors=True)
    except Exception:
        pass

    return RedirectResponse("/admin/invites", status_code=303)

@router.post("/admin/users/toggle-admin")
async def admin_toggle_admin(
    request: Request,
    user_id: int = Form(...),
    make_admin: int = Form(...),
):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    db   = db_getter()
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    if user_id == uid:  # can't change your own admin status
        return RedirectResponse("/admin/invites", status_code=303)
    db._con.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if make_admin else 0, user_id))
    db._con.commit()
    return RedirectResponse("/admin/invites", status_code=303)

@router.post("/admin/invites/delete")
async def admin_delete_invite(
    request: Request,
    token:   str = Form(...),
):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        return RedirectResponse("/login", status_code=303)
    db   = db_getter()
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        return RedirectResponse("/", status_code=303)
    db.delete_invite(token)
    return RedirectResponse("/admin/invites", status_code=303)


@router.get("/admin/backup-db")
async def backup_db(request: Request):
    import subprocess
    from datetime import date
    from fastapi.responses import StreamingResponse
    from app.auth import get_session_user_id
    uid  = get_session_user_id(request)
    user = db_getter().get_user(uid) if uid else None
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Admin only")
    db_path = db_getter().path
    fname   = f"Ascent-{date.today()}.ascentdb.gz"
    def generate():
        proc = subprocess.Popen(["gzip", "-c", db_path], stdout=subprocess.PIPE)
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            yield chunk
        proc.wait()
    return StreamingResponse(
        generate(),
        media_type="application/gzip",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


