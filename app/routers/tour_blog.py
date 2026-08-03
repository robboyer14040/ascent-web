"""routers/tour_blog.py — Blog / coffee-table-book presentation of a shared tour.

A linear, reader-friendly view of an existing shared tour: a Table-of-Contents plus a
per-stage story with author-written prose, embedded photos/videos, reader comments, and a
one-click "Publish" to a printable PDF book. It reuses the SAME share token as
`/tours/share/{token}` and imports the tour data helpers from `routers/tours.py`.

Blog content and comments are keyed by (tour_id, owner_user_id) so they survive a token
regeneration. Public routes take the token and run no auth check (the token is the
authorization); owner-only routes additionally require the session user to be the share owner.
"""

import base64
import html as _html
import io
import json
import re
import sqlite3
import threading
import time as _time
import uuid as _uuid
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.auth import get_session_user_id
from app.routers.tours import (
    _ensure_tables,
    _completions_for_user,
    build_activity,
)

router = APIRouter()
db_getter: Callable = None
templates = None


# ── DB setup ──────────────────────────────────────────────────────────────────

def _ensure_blog_tables(con):
    """Lazily create the blog-owned tables (same pattern as tours._ensure_tables)."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_blog (
            tour_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            intro      TEXT,
            cover_media TEXT,
            updated_at INTEGER,
            PRIMARY KEY (tour_id, user_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_blog_stage (
            tour_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            stage_id   INTEGER NOT NULL,
            body       TEXT,
            updated_at INTEGER,
            PRIMARY KEY (tour_id, user_id, stage_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_blog_stage_media (
            tour_id     INTEGER NOT NULL,
            user_id     INTEGER NOT NULL,
            stage_id    INTEGER NOT NULL,
            order_json  TEXT,
            hidden_json TEXT,
            updated_at  INTEGER,
            PRIMARY KEY (tour_id, user_id, stage_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS tour_comments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            tour_id       INTEGER NOT NULL,
            owner_user_id INTEGER NOT NULL,
            stage_num     INTEGER,
            author_name   TEXT    NOT NULL,
            body          TEXT    NOT NULL,
            parent_id     INTEGER,
            is_owner      INTEGER NOT NULL DEFAULT 0,
            created_at    INTEGER NOT NULL
        )
    """)
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_tour_comments ON tour_comments(tour_id, owner_user_id)"
    )
    con.commit()


# ── Markdown-lite (single source, used by the page JSON and the PDF template) ──

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+|/[^\s)]*)\)")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_MD_ITAL = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", re.DOTALL)


def render_md(text: Optional[str]) -> str:
    """Render markdown-lite prose to safe HTML: escape everything first, then support
    **bold**, *italic*, [text](url) links (http/https or root-relative only), and
    blank-line paragraphs with single-newline <br>. No client parser — page and PDF
    both consume this output."""
    if not text:
        return ""
    esc = _html.escape(text.strip())
    # Links first: re-inject a real <a>; the label is already escaped.
    esc = _MD_LINK.sub(
        lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener nofollow">{m.group(1)}</a>',
        esc,
    )
    esc = _MD_BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", esc)
    esc = _MD_ITAL.sub(lambda m: f"<em>{m.group(1)}</em>", esc)
    paras = re.split(r"\n\s*\n", esc)
    return "".join(
        f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras if p.strip()
    )


# ── Token / ownership helpers ─────────────────────────────────────────────────

def _resolve_blog_share(con, token: str):
    """Return (tour_id, owner_uid, attempt_id, title) for a valid token, else 404."""
    row = con.execute(
        "SELECT ts.tour_id, ts.user_id, ts.attempt_id, t.title "
        "FROM tour_shares ts JOIN tours t ON t.id = ts.tour_id WHERE ts.token=?",
        (token,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Share link not found or revoked")
    return row[0], row[1], row[2], row[3]


def _require_owner(con, token: str, request: Request):
    """Resolve the token and assert the session user is the share owner.
    Returns (tour_id, owner_uid, attempt_id, title). Raises 401/403 otherwise."""
    tour_id, owner_uid, attempt_id, title = _resolve_blog_share(con, token)
    session_uid = get_session_user_id(request)
    if session_uid is None:
        raise HTTPException(401, "Sign in required")
    if session_uid != owner_uid:
        raise HTTPException(403, "Only the tour owner can edit this blog")
    return tour_id, owner_uid, attempt_id, title


def _media_layout(con, tour_id: int, owner_uid: int) -> dict:
    """Per-stage blog-only media layout: {stage_id: {"order": [url,…], "hidden": [url,…]}}.
    Both lists are keyed by media URL (the stable media identity)."""
    out: dict = {}
    for sid, order_json, hidden_json in con.execute(
        "SELECT stage_id, order_json, hidden_json FROM tour_blog_stage_media "
        "WHERE tour_id=? AND user_id=?",
        (tour_id, owner_uid),
    ).fetchall():
        out[sid] = {
            "order": json.loads(order_json) if order_json else [],
            "hidden": json.loads(hidden_json) if hidden_json else [],
        }
    return out


def _apply_media_layout(items, get_url, order: list, hidden: list):
    """Order + filter a media sequence by a blog layout. `get_url(item)` returns an
    item's URL. Items in `order` come first (in that order); the rest keep their natural
    order after them; items whose URL is in `hidden` are dropped. Shared by the page
    payload and the PDF so both agree."""
    hide = set(hidden or [])
    rank = {u: i for i, u in enumerate(order or [])}
    kept = [it for it in items if get_url(it) not in hide]
    return sorted(kept, key=lambda it: rank.get(get_url(it), len(rank)))


def _stage_rows(con, tour_id: int):
    return con.execute(
        "SELECT id, stage_num, name, distance_mi, climb_ft, start_lat, start_lon, alt_override "
        "FROM tour_stages WHERE tour_id=? ORDER BY stage_num",
        (tour_id,),
    ).fetchall()


# ── Page ──────────────────────────────────────────────────────────────────────

@router.get("/tours/blog/{token}", response_class=HTMLResponse)
async def tour_blog_page(token: str, request: Request):
    """Public blog page. is_owner enables inline editing, Publish-PDF, and moderation."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _attempt_id, title = _resolve_blog_share(con, token)
        user_row = con.execute(
            "SELECT username FROM users WHERE id=?", (owner_uid,)
        ).fetchone()
        display_name = user_row[0] if user_row else "Unknown"
    finally:
        con.close()

    is_owner = (get_session_user_id(request) == owner_uid)
    try:
        use_metric = db_getter().get_user_profile(owner_uid).get("use_metric", False)
    except Exception:
        use_metric = False

    return templates.TemplateResponse("tour_blog.html", {
        "request":      request,
        "token":        token,
        "tour_title":   title,
        "display_name": display_name,
        "share_uid":    owner_uid,
        "is_owner":     is_owner,
        "use_metric":   use_metric,
    })


# ── Consolidated content ──────────────────────────────────────────────────────

@router.get("/tours/blog/{token}/content")
async def tour_blog_content(token: str):
    """One JSON payload driving both the page and the PDF: stages + completions,
    the tour intro + cover, per-stage author prose (rendered HTML + raw), and an
    AI-summary seed the owner can prefill from."""
    con = sqlite3.connect(db_getter().path, timeout=15)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, attempt_id, title = _resolve_blog_share(con, token)

        srows = _stage_rows(con, tour_id)
        stages = [{
            "id":           r[0],
            "stage_num":    r[1],
            "name":         r[2],
            "distance_mi":  r[3],
            "climb_ft":     r[4],
            "start_lat":    r[5],
            "start_lon":    r[6],
            "alt_override": r[7],
            "completion":   None,
        } for r in srows]

        completions, _, _ = _completions_for_user(con, tour_id, owner_uid, stages, attempt_id)
        for s in stages:
            s["completion"] = completions.get(s["id"])

        # Blog prose keyed by (tour_id, owner_uid).
        blog = con.execute(
            "SELECT intro, cover_media FROM tour_blog WHERE tour_id=? AND user_id=?",
            (tour_id, owner_uid),
        ).fetchone()
        intro_raw = blog[0] if blog else ""
        cover_media = blog[1] if blog else None

        body_rows = con.execute(
            "SELECT stage_id, body FROM tour_blog_stage WHERE tour_id=? AND user_id=?",
            (tour_id, owner_uid),
        ).fetchall()
        bodies = {br[0]: br[1] for br in body_rows}

        # Per-stage blog-only media layout (order + hidden), keyed by media URL.
        layout = _media_layout(con, tour_id, owner_uid)

        # AI-summary seeds: tour-level for the intro, per-stage for each section.
        tour_ai = con.execute(
            "SELECT ai_summary FROM tours WHERE id=?", (tour_id,)
        ).fetchone()
        for s in stages:
            # Default the stage note to the Strava activity description; once the
            # owner saves an edit it takes over. One note, not two.
            saved = (bodies.get(s["id"]) or "").strip()
            raw = saved or _strava_notes(con, (s["completion"] or {}).get("activity_id"))
            s["body_raw"] = raw
            s["body_html"] = render_md(raw)
            seed = con.execute(
                "SELECT summary FROM tour_stage_ai_summary WHERE stage_id=?", (s["id"],)
            ).fetchone()
            s["ai_seed"] = seed[0] if seed else ""
            lay = layout.get(s["id"], {})
            s["media_order"] = lay.get("order", [])
            s["media_hidden"] = lay.get("hidden", [])

        return JSONResponse({
            "id":          tour_id,
            "title":       title,
            "intro_raw":   intro_raw or "",
            "intro_html":  render_md(intro_raw),
            "intro_seed":  (tour_ai[0] if tour_ai and tour_ai[0] else ""),
            "cover_media": cover_media,
            "stages":      stages,
        })
    finally:
        con.close()


# ── Owner editing ─────────────────────────────────────────────────────────────

@router.put("/tours/blog/{token}/intro")
async def tour_blog_set_intro(token: str, request: Request, payload: dict = Body(...)):
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _require_owner(con, token, request)
        intro = (payload.get("intro") or "").strip()
        cover = payload.get("cover_media")
        con.execute(
            "INSERT INTO tour_blog (tour_id, user_id, intro, cover_media, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(tour_id, user_id) DO UPDATE SET "
            "intro=excluded.intro, cover_media=excluded.cover_media, updated_at=excluded.updated_at",
            (tour_id, owner_uid, intro, cover, int(_time.time())),
        )
        con.commit()
        return JSONResponse({"intro_html": render_md(intro), "cover_media": cover})
    finally:
        con.close()


@router.put("/tours/blog/{token}/stage/{stage_id}")
async def tour_blog_set_stage(token: str, stage_id: int, request: Request,
                              payload: dict = Body(...)):
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _require_owner(con, token, request)
        # Guard: the stage must belong to this tour.
        ok = con.execute(
            "SELECT 1 FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_id)
        ).fetchone()
        if not ok:
            raise HTTPException(404, "Stage not found")
        body = (payload.get("body") or "").strip()
        con.execute(
            "INSERT INTO tour_blog_stage (tour_id, user_id, stage_id, body, updated_at) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(tour_id, user_id, stage_id) DO UPDATE SET "
            "body=excluded.body, updated_at=excluded.updated_at",
            (tour_id, owner_uid, stage_id, body, int(_time.time())),
        )
        con.commit()
        return JSONResponse({"body_html": render_md(body)})
    finally:
        con.close()


@router.put("/tours/blog/{token}/stage/{stage_id}/media")
async def tour_blog_set_stage_media(token: str, stage_id: int, request: Request,
                                    payload: dict = Body(...)):
    """Owner-only — save the blog-only media layout for a stage: an ordered list of media
    URLs plus a hidden list. The underlying activity media is untouched."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _require_owner(con, token, request)
        ok = con.execute(
            "SELECT 1 FROM tour_stages WHERE id=? AND tour_id=?", (stage_id, tour_id)
        ).fetchone()
        if not ok:
            raise HTTPException(404, "Stage not found")
        order = [str(u) for u in (payload.get("order") or [])]
        hidden = [str(u) for u in (payload.get("hidden") or [])]
        con.execute(
            "INSERT INTO tour_blog_stage_media (tour_id, user_id, stage_id, order_json, hidden_json, updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(tour_id, user_id, stage_id) DO UPDATE SET "
            "order_json=excluded.order_json, hidden_json=excluded.hidden_json, updated_at=excluded.updated_at",
            (tour_id, owner_uid, stage_id, json.dumps(order), json.dumps(hidden), int(_time.time())),
        )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


# ── Comments (open readers post; owner moderates) ─────────────────────────────

def _comment_dict(row) -> dict:
    return {
        "id":          row[0],
        "stage_num":   row[1],
        "author_name": row[2],
        "body":        row[3],
        "parent_id":   row[4],
        "is_owner":    bool(row[5]),
        "created_at":  row[6],
    }


@router.get("/tours/blog/{token}/comments")
async def tour_blog_comments(token: str):
    """Public — all comments for this blog, oldest first. Threading (one level) is
    done client-side via parent_id."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _resolve_blog_share(con, token)
        rows = con.execute(
            "SELECT id, stage_num, author_name, body, parent_id, is_owner, created_at "
            "FROM tour_comments WHERE tour_id=? AND owner_user_id=? ORDER BY created_at ASC, id ASC",
            (tour_id, owner_uid),
        ).fetchall()
        return JSONResponse({"comments": [_comment_dict(r) for r in rows]})
    finally:
        con.close()


@router.post("/tours/blog/{token}/comments")
async def tour_blog_add_comment(token: str, request: Request, payload: dict = Body(...)):
    """Public — post a comment. Honeypot `website` must be empty. If the poster is the
    signed-in owner, the comment is flagged is_owner and named with their username."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _resolve_blog_share(con, token)

        # Honeypot: real users never fill this hidden field.
        if (payload.get("website") or "").strip():
            raise HTTPException(400, "Rejected")

        name = (payload.get("author_name") or "").strip()[:80]
        body = (payload.get("body") or "").strip()[:4000]
        if not body:
            raise HTTPException(400, "Comment cannot be empty")

        parent_id = payload.get("parent_id")
        if parent_id is not None:
            # Only accept a parent that belongs to this same blog.
            ok = con.execute(
                "SELECT 1 FROM tour_comments WHERE id=? AND tour_id=? AND owner_user_id=?",
                (parent_id, tour_id, owner_uid),
            ).fetchone()
            if not ok:
                parent_id = None

        stage_num = payload.get("stage_num")
        if stage_num is not None:
            try:
                stage_num = int(stage_num)
            except (TypeError, ValueError):
                stage_num = None

        session_uid = get_session_user_id(request)
        is_owner = 1 if session_uid == owner_uid else 0
        if is_owner:
            urow = con.execute("SELECT username FROM users WHERE id=?", (owner_uid,)).fetchone()
            name = (urow[0] if urow else name) or "Author"
        if not name:
            name = "Anonymous"

        cur = con.execute(
            "INSERT INTO tour_comments "
            "(tour_id, owner_user_id, stage_num, author_name, body, parent_id, is_owner, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (tour_id, owner_uid, stage_num, name, body, parent_id, is_owner, int(_time.time())),
        )
        con.commit()
        row = con.execute(
            "SELECT id, stage_num, author_name, body, parent_id, is_owner, created_at "
            "FROM tour_comments WHERE id=?", (cur.lastrowid,),
        ).fetchone()
        return JSONResponse({"comment": _comment_dict(row)})
    finally:
        con.close()


@router.delete("/tours/blog/{token}/comments/{comment_id}")
async def tour_blog_delete_comment(token: str, comment_id: int, request: Request):
    """Owner-only — delete a comment and its direct replies."""
    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, _a, _t = _require_owner(con, token, request)
        con.execute(
            "DELETE FROM tour_comments WHERE owner_user_id=? AND tour_id=? AND (id=? OR parent_id=?)",
            (owner_uid, tour_id, comment_id, comment_id),
        )
        con.commit()
        return JSONResponse({"ok": True})
    finally:
        con.close()


# ── PDF export (square 8×8 coffee-table book) ─────────────────────────────────

_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm")


def _stage_points(con, stage_id: int):
    rows = con.execute(
        "SELECT lat, lon, alt_ft FROM tour_stage_points WHERE stage_id=? ORDER BY seq",
        (stage_id,),
    ).fetchall()
    return [[r[0], r[1], r[2]] for r in rows]


def _strava_notes(con, activity_id) -> str:
    """The completed activity's Strava description — used as the default blog stage
    note (the owner can then edit it into saved prose)."""
    if not activity_id:
        return ""
    old_rf = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        arow = con.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
        return (build_activity(arow).get("notes") or "") if arow else ""
    except Exception:
        return ""
    finally:
        con.row_factory = old_rf


def _weather_location_blocking(activity_id) -> dict:
    """Weather + location for a completed stage, for the (blocking) PDF build. Reuses the
    shared activity resolver, which returns cached values instantly and otherwise fetches
    them once and caches to the DB. Runs in a worker thread with no event loop, so a fresh
    asyncio loop is safe. Never raises — a missing/failed lookup just yields no line."""
    import asyncio
    from app.routers.weather import activity_weather_location
    try:
        return asyncio.run(activity_weather_location(activity_id)) or {}
    except Exception:
        return {}


def _media_file(con, url: str) -> Optional[Path]:
    """Resolve a media URL (/photos/{aid}/{fn} or /user-uploads/{aid}/{fn}) to a local
    file on disk, or None if it can't be resolved / doesn't exist."""
    from app.routers.photos import _photos_dir, _user_uploads_dir
    parts = url.strip("/").split("/")
    if len(parts) < 3:
        return None
    kind, aid, fn = parts[0], parts[1], parts[2]
    try:
        if kind == "photos":
            row = con.execute(
                "SELECT strava_activity_id FROM activities WHERE id=?", (aid,)
            ).fetchone()
            if not row or not row[0]:
                return None
            p = _photos_dir(row[0]) / fn
        elif kind == "user-uploads":
            p = _user_uploads_dir(int(aid)) / fn
        else:
            return None
    except Exception:
        return None
    return p if p.exists() else None


def _img_data_uri(path: Path, max_px: int = 820, quality: int = 72):
    """Downscale a local image and return (data_uri, is_portrait). None on failure.
    Kept modest (≈print DPI for an 8in page) so a photo-heavy book stays within the
    small deploy box's memory."""
    from PIL import Image
    try:
        img = Image.open(path)
        img = img.convert("RGB")
        w, h = img.size
        portrait = h > w * 1.1
        img.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        return uri, portrait
    except Exception:
        return None


def _pdf_stage_media(con, activity_id: int, order=None, hidden=None):
    """Local, downscaled still-images for a completed stage. Strava videos are kept as
    their poster/thumbnail frame (with caption) — a printed book can't play video, but
    the still + description mirror what the blog shows. The blog-only media layout
    (order/hidden, keyed by URL) is applied before downscaling so hidden photos are
    skipped entirely."""
    out = []          # (url, file, caption)
    row = con.execute(
        "SELECT local_media_items_json, local_media_captions_json "
        "FROM activities WHERE id=?", (activity_id,),
    ).fetchone()
    if row:
        items = json.loads(row[0]) if row[0] else []
        caps = json.loads(row[1]) if row[1] else {}
        for fn in items:
            url = f"/photos/{activity_id}/{fn}"
            f = _media_file(con, url)
            if f:
                out.append((url, f, (caps.get(fn) if isinstance(caps, dict) else "") or ""))
    try:
        for fn, cap in con.execute(
            "SELECT filename, caption FROM user_media WHERE activity_id=?", (activity_id,)
        ).fetchall():
            if fn.lower().endswith(_VIDEO_EXTS):
                continue
            url = f"/user-uploads/{activity_id}/{fn}"
            f = _media_file(con, url)
            if f:
                out.append((url, f, cap or ""))
    except Exception:
        pass

    out = _apply_media_layout(out, lambda it: it[0], order or [], hidden or [])

    figs = []
    for _url, f, cap in out:
        res = _img_data_uri(f)
        if res:
            figs.append({"data_uri": res[0], "caption": cap, "portrait": res[1]})
    return figs


def _pdf_context(con, token: str, progress=None) -> dict:
    """Build the full template context for the PDF book (no WeasyPrint needed — split
    out so it can be exercised in tests / local verification). Fetches map tiles and
    downscales photos, so it is slow and blocking; callers run it in a threadpool.
    Optional progress(current, total, phase) is called as each stage is built."""
    from app.static_map import render_route_png, elevation_svg
    if True:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        tour_id, owner_uid, attempt_id, title = _resolve_blog_share(con, token)
        urow = con.execute("SELECT username FROM users WHERE id=?", (owner_uid,)).fetchone()
        author = urow[0] if urow else "Unknown"
        try:
            metric = db_getter().get_user_profile(owner_uid).get("use_metric", False)
        except Exception:
            metric = False

        srows = _stage_rows(con, tour_id)
        stages = [{
            "id": r[0], "stage_num": r[1], "name": r[2],
            "distance_mi": r[3], "climb_ft": r[4],
            "start_lat": r[5], "start_lon": r[6], "alt_override": r[7],
            "completion": None,
        } for r in srows]
        completions, _, _ = _completions_for_user(con, tour_id, owner_uid, stages, attempt_id)
        for s in stages:
            s["completion"] = completions.get(s["id"])

        blog = con.execute(
            "SELECT intro, cover_media FROM tour_blog WHERE tour_id=? AND user_id=?",
            (tour_id, owner_uid),
        ).fetchone()
        intro_html = render_md(blog[0]) if blog and blog[0] else ""
        cover_uri = None
        if blog and blog[1]:
            f = _media_file(con, blog[1])
            if f:
                res = _img_data_uri(f, max_px=1600, quality=80)
                cover_uri = res[0] if res else None

        bodies = {br[0]: br[1] for br in con.execute(
            "SELECT stage_id, body FROM tour_blog_stage WHERE tour_id=? AND user_id=?",
            (tour_id, owner_uid),
        ).fetchall()}
        layout = _media_layout(con, tour_id, owner_uid)

        def unit_dist(mi): return f"{mi*1.60934:.1f} km" if metric else f"{mi:.1f} mi"
        def unit_climb(ft): return f"{round(ft*0.3048)} m" if metric else f"{round(ft)} ft"
        def unit_temp(f): return f"{(f-32)*5/9:.1f} °C" if metric else f"{round(f)} °F"
        def unit_wind(k): return f"{round(k)} km/h" if metric else f"{round(k/1.60934)} mph"
        def unit_precip(mm): return f"{mm} mm" if metric else f'{mm/25.4:.2f}"'

        def weather_location(activity_id):
            """(location_text, weather_text) for a completed stage — plain text for print."""
            wl = _weather_location_blocking(activity_id)
            loc = wl.get("locations") or ""
            wx = ""
            w = wl.get("weather") or {}
            if isinstance(w, dict) and w.get("description"):
                parts = [w["description"]]
                if w.get("avg_temp_f")   is not None: parts.append(unit_temp(w["avg_temp_f"]))
                if w.get("avg_wind_kph") is not None: parts.append("Wind " + unit_wind(w["avg_wind_kph"]))
                if w.get("precip_mm"):                parts.append(unit_precip(w["precip_mm"]))
                wx = " · ".join(parts)
            return loc, wx

        stat_stages = [s for s in stages if s["alt_override"] != 1]
        tot_dist = sum((s["completion"] or {}).get("distance_mi") or s["distance_mi"] or 0 for s in stat_stages)
        tot_climb = sum((s["completion"] or {}).get("climb_ft") or s["climb_ft"] or 0 for s in stat_stages)
        done = sum(1 for s in stat_stages if s["completion"])

        tile_cache: dict = {}
        pdf_stages = []
        n_stages = len(stages)
        for idx, s in enumerate(stages):
            if progress:
                progress(idx, n_stages, f"Building stage {idx + 1} of {n_stages}…")
            pts = _stage_points(con, s["id"])
            c = s["completion"]
            chips = [("Distance", unit_dist((c or {}).get("distance_mi") or s["distance_mi"] or 0)),
                     ("Ascent", unit_climb((c or {}).get("climb_ft") or s["climb_ft"] or 0))]
            if c and c.get("moving_s"):
                hh = int(c["moving_s"] // 3600); mm = int((c["moving_s"] % 3600) // 60)
                chips.append(("Moving", f"{hh}h {mm:02d}m"))
            map_uri = None
            if len(pts) >= 2:
                color = (22, 163, 74) if c else (30, 64, 175)
                png = render_route_png([(p[0], p[1]) for p in pts],
                                       line_color=color, tile_cache=tile_cache)
                map_uri = "data:image/png;base64," + base64.b64encode(png).decode()
            # Stage note = saved prose, else the Strava activity description (default).
            saved = (bodies.get(s["id"]) or "").strip()
            body_raw = saved or _strava_notes(con, c["activity_id"] if c and c.get("activity_id") else None)
            loc_text, wx_text = (weather_location(c["activity_id"])
                                 if c and c.get("activity_id") else ("", ""))
            pdf_stages.append({
                "stage_num": s["stage_num"], "name": s["name"],
                "date": (c or {}).get("date") if c else None,
                "chips": chips,
                "map_uri": map_uri,
                "location": loc_text,
                "weather": wx_text,
                "elev_svg": elevation_svg(pts, metric) if len(pts) >= 2 else "",
                "body_html": render_md(body_raw),
                "figs": _pdf_stage_media(
                    con, c["activity_id"],
                    order=layout.get(s["id"], {}).get("order", []),
                    hidden=layout.get(s["id"], {}).get("hidden", []),
                ) if c and c.get("activity_id") else [],
            })

        return {
            "title": title, "author": author, "intro_html": intro_html, "cover_uri": cover_uri,
            "toc": [{"stage_num": s["stage_num"], "name": s["name"],
                     "done": bool(s["completion"])} for s in stages],
            "tot_dist": unit_dist(tot_dist), "tot_climb": unit_climb(tot_climb),
            "done": done, "total": len(stat_stages),
            "stages": pdf_stages,
        }


def _render_pdf(token: str, progress=None):
    """Build the book and return (pdf_bytes, filename). Raises RuntimeError(501-style)
    if WeasyPrint is unavailable. Shared by the sync route and the background job."""
    try:
        import weasyprint  # lazy — heavy native deps
    except Exception:
        raise RuntimeError("PDF export is unavailable on this server (WeasyPrint not installed).")

    con = sqlite3.connect(db_getter().path, timeout=30)
    try:
        ctx = _pdf_context(con, token, progress=progress)
    finally:
        con.close()

    if progress:
        progress(ctx["total"], ctx["total"], "Rendering PDF…")
    html_str = templates.env.get_template("tour_blog_pdf.html").render(**ctx)
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", ctx["title"] or "tour").strip("_") or "tour"
    return pdf_bytes, f"{safe}.pdf"


@router.get("/tours/blog/{token}/pdf")
def tour_blog_pdf(token: str):
    """Synchronous render — kept for a direct link / backward compatibility. The UI uses
    the job flow below so it can show progress. Comments are excluded from the book."""
    try:
        pdf_bytes, filename = _render_pdf(token)
    except RuntimeError as e:
        raise HTTPException(501, str(e))
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


# ── Background PDF jobs (so the UI can show a progress bar) ────────────────────
# Jobs live in memory — a book render is a one-shot, few-minute task tied to a single
# download, so there's no need to persist it. Each job holds the finished bytes until
# it's downloaded or reaped.

_pdf_jobs: dict = {}
_pdf_jobs_lock = threading.Lock()
_PDF_JOB_TTL = 1800          # reap finished/failed jobs after 30 min


def _reap_jobs():
    now = _time.time()
    with _pdf_jobs_lock:
        for jid in [k for k, v in _pdf_jobs.items()
                    if v.get("done_at") and now - v["done_at"] > _PDF_JOB_TTL]:
            _pdf_jobs.pop(jid, None)


def _run_pdf_job(job_id: str, token: str):
    def progress(cur, total, phase):
        with _pdf_jobs_lock:
            j = _pdf_jobs.get(job_id)
            if j:
                j.update(current=cur, total=total, phase=phase)
    try:
        pdf_bytes, filename = _render_pdf(token, progress=progress)
        with _pdf_jobs_lock:
            j = _pdf_jobs.get(job_id)
            if j:
                j.update(status="ready", pdf=pdf_bytes, filename=filename,
                         phase="Done", done_at=_time.time())
    except Exception as e:
        with _pdf_jobs_lock:
            j = _pdf_jobs.get(job_id)
            if j:
                j.update(status="error", error=str(e), done_at=_time.time())


@router.post("/tours/blog/{token}/pdf/start")
def tour_blog_pdf_start(token: str):
    """Kick off a background PDF build; returns a job_id to poll."""
    try:
        import weasyprint  # noqa: F401 — fail fast before spawning a thread
    except Exception:
        raise HTTPException(501, "PDF export is unavailable on this server (WeasyPrint not installed).")

    con = sqlite3.connect(db_getter().path, timeout=10)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        _resolve_blog_share(con, token)      # 404 on a bad token before we start
    finally:
        con.close()

    _reap_jobs()
    job_id = _uuid.uuid4().hex
    with _pdf_jobs_lock:
        _pdf_jobs[job_id] = {"status": "running", "current": 0, "total": 0,
                             "phase": "Starting…", "created_at": _time.time()}
    threading.Thread(target=_run_pdf_job, args=(job_id, token), daemon=True).start()
    return JSONResponse({"job_id": job_id})


@router.get("/tours/blog/{token}/pdf/status/{job_id}")
def tour_blog_pdf_status(token: str, job_id: str):
    with _pdf_jobs_lock:
        j = _pdf_jobs.get(job_id)
        if not j:
            raise HTTPException(404, "Job not found or expired")
        total = j.get("total") or 0
        cur = j.get("current") or 0
        pct = int(cur / total * 100) if total else (100 if j["status"] == "ready" else 0)
        return JSONResponse({
            "status": j["status"], "current": cur, "total": total,
            "percent": pct, "phase": j.get("phase", ""), "error": j.get("error"),
        })


@router.get("/tours/blog/{token}/pdf/download/{job_id}")
def tour_blog_pdf_download(token: str, job_id: str):
    with _pdf_jobs_lock:
        j = _pdf_jobs.get(job_id)
        if not j:
            raise HTTPException(404, "Job not found or expired")
        if j["status"] == "error":
            raise HTTPException(500, j.get("error") or "PDF render failed")
        if j["status"] != "ready":
            raise HTTPException(425, "Not ready")     # 425 Too Early
        pdf_bytes, filename = j["pdf"], j["filename"]
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
