"""
routers/photos.py — On-demand photo/video resolution + serving.

Strava media objects:
  type=1  photo  — download JPEG, serve locally
  type=2  video  — download JPEG thumbnail, store HLS URL in local_video_urls_json

Priority for each photo:
  1. Already in support/photos/{strava_id}/ → serve directly
  2. Found in ~/Documents/media/ (legacy macOS app folder) → copy → update DB → serve
  3. Not found locally → download from Strava API → save → update DB → serve
"""

import os, json, shutil, sqlite3, time
from pathlib import Path
from typing import Optional

import httpx
import logging
from fastapi import APIRouter, HTTPException
log = logging.getLogger('photos')
from fastapi.responses import FileResponse

router = APIRouter()
db_getter = None   # injected by main.py

# ── paths ─────────────────────────────────────────────────────────────────────

def _db_path() -> str:
    return os.environ.get("ASCENT_DB_PATH", "")

def _photos_dir(strava_id) -> Path:
    d = Path(_db_path()).parent / "support" / "photos" / str(strava_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _legacy_dirs() -> list[Path]:
    candidates = [
        Path.home() / "Documents" / "media",
        Path.home() / "Library" / "Application Support" / "Ascent" / "media",
        Path(_db_path()).parent / "media",
    ]
    return [p for p in candidates if p.exists()]

# ── token ─────────────────────────────────────────────────────────────────────

async def _fresh_token() -> Optional[str]:
    try:
        p = Path(_db_path()).parent / "strava_tokens.json"
        if not p.exists():
            return None
        tok = json.loads(p.read_text())
        if tok.get("expires_at", 0) > time.time() + 60:
            return tok.get("access_token")
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://www.strava.com/oauth/token", data={
                "client_id":     os.environ.get("STRAVA_CLIENT_ID", ""),
                "client_secret": os.environ.get("STRAVA_CLIENT_SECRET", ""),
                "grant_type":    "refresh_token",
                "refresh_token": tok["refresh_token"],
            })
            if r.status_code == 200:
                d = r.json()
                tok.update({"access_token": d["access_token"],
                             "refresh_token": d.get("refresh_token", tok["refresh_token"]),
                             "expires_at": d["expires_at"]})
                p.write_text(json.dumps(tok, indent=2))
                return tok["access_token"]
    except Exception:
        pass
    return None

# ── DB ────────────────────────────────────────────────────────────────────────

def _ensure_video_column():
    """Add local_video_urls_json column if it doesn't exist yet."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute("ALTER TABLE activities ADD COLUMN local_video_urls_json TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    finally:
        con.close()

def _get_info(activity_id: int) -> Optional[dict]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT strava_activity_id, local_media_items_json, local_video_urls_json "
            "FROM activities WHERE id=?",
            (activity_id,)).fetchone()
        if not row:
            return None
        filenames = []
        if row[1]:
            try: filenames = json.loads(row[1])
            except Exception: pass
        video_urls = []
        if row[2]:
            try: video_urls = json.loads(row[2])
            except Exception: pass
        return {"strava_id": row[0], "filenames": filenames, "video_urls": video_urls}
    except sqlite3.OperationalError:
        # Column may not exist yet; fall back
        row = con.execute(
            "SELECT strava_activity_id, local_media_items_json FROM activities WHERE id=?",
            (activity_id,)).fetchone()
        if not row:
            return None
        filenames = []
        if row[1]:
            try: filenames = json.loads(row[1])
            except Exception: pass
        return {"strava_id": row[0], "filenames": filenames, "video_urls": []}
    finally:
        con.close()

def _save_media(activity_id: int, filenames: list[str], video_urls: list[str]):
    """Persist photo filenames and video HLS URLs to the DB."""
    _ensure_video_column()
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "UPDATE activities SET local_media_items_json=?, local_video_urls_json=? WHERE id=?",
            (json.dumps(filenames), json.dumps(video_urls), activity_id))
        con.commit()
    finally:
        con.close()

# ── Strava download ───────────────────────────────────────────────────────────

async def _download_from_strava(strava_id: int, dest_dir: Path,
                                 existing: set[str]) -> tuple[list[str], list[str]]:
    """
    Fetch media from Strava for the activity.
    - type=1 (photo): download JPEG, return in filenames list
    - type=2 (video): download thumbnail JPEG, collect HLS URL
    Returns (photo_filenames, hls_video_urls).
    """
    token = await _fresh_token()
    if not token:
        log.warning(f"No Strava token for media download (activity {strava_id})")
        return [], []

    filenames  = []
    video_urls = []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"https://www.strava.com/api/v3/activities/{strava_id}/photos",
                headers={"Authorization": f"Bearer {token}"},
                params={"size": 1024, "photo_sources": "true"},
            )
            if resp.status_code != 200:
                log.warning(f"Strava photos API {resp.status_code} for {strava_id}: {resp.text[:200]}")
                return [], []
            photos = resp.json()
            if not isinstance(photos, list):
                return [], []

            for i, photo in enumerate(photos):
                media_type = photo.get("type", 1)  # 1=photo, 2=video
                urls       = photo.get("urls") or {}
                thumb_url  = (urls.get("1024") or urls.get("600") or
                              urls.get("2048") or urls.get("100") or
                              photo.get("source_url") or photo.get("url"))
                uid = photo.get("unique_id") or photo.get("id") or i

                if media_type == 2:
                    # Video: collect HLS URL, download thumbnail for panel preview
                    hls = photo.get("video_url")
                    if hls:
                        video_urls.append(hls)
                        log.info(f"Found video HLS for {strava_id}: {hls[:80]}")
                    if thumb_url:
                        filename = f"strava_{uid}.jpg"
                        if filename not in existing and not (dest_dir / filename).exists():
                            try:
                                r = await client.get(thumb_url, timeout=30, follow_redirects=True)
                                if r.status_code == 200:
                                    (dest_dir / filename).write_bytes(r.content)
                            except Exception as e:
                                log.warning(f"Video thumb download failed: {e}")
                        filenames.append(filename)
                else:
                    # Regular photo
                    if not thumb_url:
                        continue
                    ext = ".jpg"
                    for e in [".png", ".webp", ".heic"]:
                        if e in thumb_url.lower():
                            ext = e; break
                    filename = f"strava_{uid}{ext}"
                    if filename in existing or (dest_dir / filename).exists():
                        filenames.append(filename)
                        continue
                    try:
                        r = await client.get(thumb_url, timeout=30, follow_redirects=True)
                        if r.status_code == 200:
                            (dest_dir / filename).write_bytes(r.content)
                            filenames.append(filename)
                        else:
                            log.warning(f"Photo download failed {r.status_code}: {thumb_url[:80]}")
                    except Exception as e:
                        log.warning(f"Photo download exception: {e}")

    except Exception as e:
        log.error(f"_download_from_strava outer exception for {strava_id}: {e}")

    return filenames, video_urls

# ── core resolution ───────────────────────────────────────────────────────────

async def resolve_photos(activity_id: int) -> dict:
    """
    Ensure all photos for an activity are in support/photos/{strava_id}/.
    Returns {"filenames": [...], "video_urls": [...]}.
    """
    info = _get_info(activity_id)
    if not info or not info["strava_id"]:
        return {"filenames": [], "video_urls": []}

    strava_id    = int(info["strava_id"])
    db_filenames = info["filenames"]
    db_videos    = info["video_urls"]
    dest_dir     = _photos_dir(strava_id)
    legacy_dirs  = _legacy_dirs()

    resolved   = []
    still_need = []

    # Step 1: check what's already on disk
    for fname in db_filenames:
        if (dest_dir / fname).exists():
            resolved.append(fname)
        else:
            still_need.append(fname)

    # Step 2: search legacy media dirs
    remaining = []
    for fname in still_need:
        found = False
        for media_dir in legacy_dirs:
            src = media_dir / fname
            if src.exists():
                try:
                    shutil.copy2(src, dest_dir / fname)
                    resolved.append(fname)
                    found = True
                except Exception:
                    pass
                break
        if not found:
            remaining.append(fname)

    # Step 3 & 4: download from Strava if files are missing OR db has no filenames
    new_filenames = []
    new_videos    = []
    if remaining or not db_filenames:
        existing_names = {f.stem for f in dest_dir.iterdir() if f.is_file()}
        new_filenames, new_videos = await _download_from_strava(strava_id, dest_dir, existing_names)
        for fname in new_filenames:
            if fname not in resolved:
                resolved.append(fname)

    # Step 5: persist if anything changed
    db_set    = set(db_filenames)
    final     = [f for f in db_filenames if f in resolved]
    for fname in new_filenames:
        if fname not in db_set:
            final.append(fname)

    final_videos = new_videos if new_videos else db_videos

    if set(final) != set(db_filenames) or final != db_filenames or final_videos != db_videos:
        _save_media(activity_id, final, final_videos)

    return {"filenames": final, "video_urls": final_videos}

# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/activities/{activity_id}/photos")
async def get_photos(activity_id: int):
    """
    Return available photos and video HLS URLs for an activity.
    Response includes a `media` array with type info for the frontend.
    """
    result   = await resolve_photos(activity_id)
    filenames  = result["filenames"]
    video_urls = result["video_urls"]
    base_url   = f"/photos/{activity_id}/"

    # Build a unified media list. Videos appear last (matching Strava order:
    # photos first, video last). We match them by position — last N filenames
    # correspond to the N video_urls (they share the same uid ordering).
    # Simpler: tag the last len(video_urls) filenames as videos if counts match.
    media = []
    n_vid = len(video_urls)
    n_img = len(filenames) - n_vid  # photos come first

    for i, fname in enumerate(filenames):
        if n_vid > 0 and i >= n_img:
            vid_idx = i - n_img
            media.append({
                "url":     base_url + fname,
                "type":    "video",
                "hls_url": video_urls[vid_idx],
            })
        else:
            media.append({"url": base_url + fname, "type": "image"})

    return {
        "photos":   filenames,        # backward compat
        "base_url": base_url,
        "media":    media,
        "video_urls": video_urls,
    }


@router.get("/photos/{activity_id}/{filename}")
async def serve_photo(activity_id: int, filename: str):
    """Serve a photo/thumbnail from support/photos/{strava_id}/."""
    info = _get_info(activity_id)
    if not info or not info["strava_id"]:
        raise HTTPException(404, "Activity not found")

    dest_dir  = _photos_dir(info["strava_id"])
    file_path = dest_dir / filename

    # Security: no path traversal
    try:
        file_path.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Forbidden")

    if not file_path.exists():
        await resolve_photos(activity_id)

    if not file_path.exists():
        raise HTTPException(404, f"Photo not found: {filename}")

    fn = filename.lower()
    if   fn.endswith(".png"):  media_type = "image/png"
    elif fn.endswith(".webp"): media_type = "image/webp"
    elif fn.endswith(".heic"): media_type = "image/heic"
    else:                      media_type = "image/jpeg"

    return FileResponse(file_path, media_type=media_type)
