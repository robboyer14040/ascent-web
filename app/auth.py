"""
auth.py — Authentication helpers for Ascent multi-user mode.

Provides:
  - Password hashing (bcrypt via passlib)
  - Session management (signed cookies via itsdangerous)
  - User CRUD helpers
  - Invite token generation
  - FastAPI dependency: get_current_user (raises 302 to /login if not authed)
"""

import os
import secrets
import time
from typing import Optional

from fastapi import Request, HTTPException

# ── Password hashing ──────────────────────────────────────────────────────────
# Use bcrypt directly (works with bcrypt 4.x and 5.x), fall back to pbkdf2
try:
    import bcrypt as _bcrypt
    def hash_password(plain: str) -> str:
        return _bcrypt.hashpw(plain.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
    def verify_password(plain: str, hashed: str) -> bool:
        try:
            return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False
except ImportError:
    import hashlib, hmac as _hmac
    def hash_password(plain: str) -> str:
        salt = secrets.token_hex(16)
        h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260000)
        return f"pbkdf2:{salt}:{h.hex()}"
    def verify_password(plain: str, hashed: str) -> bool:
        if not hashed.startswith("pbkdf2:"):
            return False
        try:
            _, salt, stored = hashed.split(":", 2)
            h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260000)
            return _hmac.compare_digest(h.hex(), stored)
        except Exception:
            return False

# ── Session helpers ───────────────────────────────────────────────────────────
SESSION_COOKIE = "ascent_session"

_DEV_SECRET_KEY = "dev-insecure-key-please-set-SECRET_KEY-change-this"


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def is_deployed() -> bool:
    """True when running on a real server rather than a developer's machine.

    Fly.io sets FLY_APP_NAME on every machine; ASCENT_HTTPS is this app's own
    documented production marker. Used to withhold dev-only conveniences that
    would be security problems once the app is reachable from the internet.
    """
    return bool(os.environ.get("FLY_APP_NAME")) or _env_true("ASCENT_HTTPS")


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "")
    if key:
        return key
    # The dev fallback is a constant published in this repo: anyone could forge a
    # session cookie for any user. Tolerable on localhost, never on a server.
    if is_deployed():
        raise RuntimeError(
            "SECRET_KEY is not set. Refusing to sign sessions with the public "
            "dev key on a deployed server — session cookies would be forgeable. "
            "Set it with:  fly secrets set SECRET_KEY=$(openssl rand -hex 32)"
        )
    global _warned_dev_key
    if not _warned_dev_key:
        _warned_dev_key = True
        import logging
        logging.getLogger("uvicorn").warning(
            "⚠ SECRET_KEY not set — using insecure dev key. Set SECRET_KEY env var!"
        )
    return _DEV_SECRET_KEY


_warned_dev_key = False

def create_session_token(user_id: int) -> str:
    """Create a signed session token encoding user_id and expiry."""
    from itsdangerous import URLSafeTimedSerializer
    s = URLSafeTimedSerializer(_secret_key())
    return s.dumps({"uid": user_id})

def decode_session_token(token: str, max_age_days: int = 30) -> Optional[int]:
    """Decode a session token. Returns user_id or None if invalid/expired."""
    try:
        from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
        s = URLSafeTimedSerializer(_secret_key())
        data = s.loads(token, max_age=max_age_days * 86400)
        return data.get("uid")
    except Exception:
        return None

def get_session_user_id(request: Request) -> Optional[int]:
    """Extract user_id from session cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return decode_session_token(token)

def set_session_cookie(response, user_id: int):
    """Attach a session cookie to a response."""
    try:
        token = create_session_token(user_id)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=30 * 86400,
            httponly=True,
            samesite="lax",
            secure=_env_true("ASCENT_HTTPS"),
        )
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").error(f"set_session_cookie failed: {e}")

def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE)

# ── Auth dependency ───────────────────────────────────────────────────────────

def require_user(request: Request) -> int:
    """Return user_id or raise HTTPException(401). Call at the top of any route that requires auth."""
    uid = get_session_user_id(request)
    if uid is None:
        raise HTTPException(401, "Not authenticated")
    return uid


def require_admin(request: Request, db) -> int:
    """Return user_id, or raise 401/403 unless the session user is an admin."""
    uid  = require_user(request)
    user = db.get_user(uid)
    if not user or not user.get("is_admin"):
        raise HTTPException(403, "Admin access required")
    return uid


def require_activity_access(request: Request, db, activity_id: int,
                            write: bool = False) -> tuple:
    """Authorize the session user against one activity. Returns (user_id, activity).

    Read access: the activity is the caller's, is unowned (legacy rows with a
    NULL user_id), or its owner opted in via share_activities — the same
    visibility rule db._build_where() applies to listings, so cross-user
    compare keeps working.
    Write access (write=True): own or unowned only; share_activities grants
    reads, never writes.
    """
    uid = require_user(request)
    act = db.get_activity(activity_id)
    if not act:
        raise HTTPException(404, "Activity not found")

    owner = act.get("user_id")
    if owner is None or owner == uid:
        return uid, act
    if not write and (db.get_user(owner) or {}).get("share_activities"):
        return uid, act
    raise HTTPException(403, "Not your activity")


def require_segment_access(request: Request, db, segment_id: int,
                           write: bool = False) -> tuple:
    """Authorize the session user against one saved segment. Returns (user_id, segment).

    A segment inherits the authorization of the activity it was drawn on, so
    share_segments owners expose reads only.
    """
    uid = require_user(request)
    seg = db.get_segment(segment_id)
    if not seg:
        raise HTTPException(404, "Segment not found")

    act = db.get_activity(seg["activity_id"])
    owner = (act or {}).get("user_id")
    if owner is None or owner == uid:
        return uid, seg
    if not write and (db.get_user(owner) or {}).get("share_segments"):
        return uid, seg
    raise HTTPException(403, "Not your segment")

# ── Invite token helpers ──────────────────────────────────────────────────────

def generate_invite_token() -> str:
    """Generate a cryptographically random invite token."""
    return secrets.token_urlsafe(32)

