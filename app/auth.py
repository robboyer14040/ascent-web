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

from fastapi import Request
from fastapi.responses import RedirectResponse

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

def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        import logging
        logging.getLogger("uvicorn").warning(
            "⚠ SECRET_KEY not set — using insecure dev key. Set SECRET_KEY env var!"
        )
        key = "dev-insecure-key-please-set-SECRET_KEY-change-this"
    return key

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
            secure=os.environ.get("ASCENT_HTTPS", "").lower() in ("1", "true", "yes"),
        )
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").error(f"set_session_cookie failed: {e}")

def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE)

# ── Auth dependency ───────────────────────────────────────────────────────────

def require_user(request: Request) -> int:
    """
    FastAPI dependency that returns user_id or raises a redirect to /login.
    Usage: user_id: int = Depends(require_user)
    """
    uid = get_session_user_id(request)
    if uid is None:
        # Store intended destination for post-login redirect
        raise _LoginRedirect(request.url.path)
    return uid

class _LoginRedirect(Exception):
    def __init__(self, next_path: str):
        self.next_path = next_path

# ── Invite token helpers ──────────────────────────────────────────────────────

def generate_invite_token() -> str:
    """Generate a cryptographically random invite token."""
    return secrets.token_urlsafe(32)

