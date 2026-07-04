"""Credential discovery for the live API tests.

Finds the user's real credentials wherever they are, in this order:
  1. a local .env file / environment variables (loaded here the way the app does).
     This is the primary path — see .env.example for the keys the live tests read:
     ANTHROPIC_API_KEY, STADIA_API_KEY, and STRAVA_REFRESH_TOKEN (+ CLIENT_ID/SECRET).
  2. the real Ascent DB at ASCENT_DB_PATH — the connected user's stored Strava
     tokens (refreshed via the app's own logic) and Anthropic key

Every getter returns "" when the credential can't be found, so each live test
can skip cleanly instead of failing.
"""

import asyncio
import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()  # pick up a local .env, same as app/main.py


def _real_db_path() -> str:
    p = os.environ.get("ASCENT_DB_PATH", "")
    return p if p and os.path.exists(p) else ""


def anthropic_key() -> str:
    """ANTHROPIC_API_KEY from env, else the first user's stored key in the real DB."""
    k = os.environ.get("ANTHROPIC_API_KEY", "")
    if k:
        return k
    p = _real_db_path()
    if p:
        try:
            con = sqlite3.connect(p)
            row = con.execute(
                "SELECT anthropic_api_key FROM users "
                "WHERE anthropic_api_key IS NOT NULL AND anthropic_api_key != '' LIMIT 1"
            ).fetchone()
            con.close()
            if row:
                return row[0]
        except Exception:
            pass
    return ""


def stadia_key() -> str:
    return os.environ.get("STADIA_API_KEY", "")


def _connected_strava_uid(path: str):
    try:
        con = sqlite3.connect(path)
        row = con.execute(
            "SELECT id FROM users "
            "WHERE strava_tokens_json IS NOT NULL AND strava_tokens_json != '' "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        con.close()
        return row[0] if row else None
    except Exception:
        return None


def _strava_token_from_env() -> str:
    """Pure-.env path: mint a fresh access token from a refresh token + client creds,
    or use an explicit STRAVA_ACCESS_TOKEN. Returns '' if not configured."""
    rt  = os.environ.get("STRAVA_REFRESH_TOKEN", "")
    cid = os.environ.get("STRAVA_CLIENT_ID", "")
    sec = os.environ.get("STRAVA_CLIENT_SECRET", "")
    if rt and cid and sec:
        try:
            import httpx
            r = httpx.post("https://www.strava.com/oauth/token",
                           data={"client_id": cid, "client_secret": sec,
                                 "grant_type": "refresh_token", "refresh_token": rt},
                           timeout=20)
            if r.status_code == 200:
                return r.json().get("access_token", "")
        except Exception:
            pass
    return os.environ.get("STRAVA_ACCESS_TOKEN", "")


def strava_token() -> str:
    """A fresh Strava access token, or ''. Sources, in order:
      1. .env — STRAVA_REFRESH_TOKEN (+ CLIENT_ID/SECRET), or STRAVA_ACCESS_TOKEN
      2. the real DB / legacy strava_tokens.json via the app's own refresh logic
         (when ASCENT_DB_PATH points at a DB with a Strava-connected user)."""
    tok = _strava_token_from_env()
    if tok:
        return tok
    try:
        from app.routers import strava as sv
        from app.db import AscentDB

        p = _real_db_path()
        uid = None
        if p:
            _db = AscentDB(p)
            sv.db_getter = lambda: _db          # wire token/creds lookups to the real DB
            uid = _connected_strava_uid(p)
        return asyncio.run(sv.get_fresh_token(user_id=uid)) or ""
    except Exception:
        return ""
