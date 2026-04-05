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
from fastapi.responses import FileResponse, StreamingResponse

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

async def _fresh_token(user_id: Optional[int] = None) -> Optional[str]:
    """Get a valid Strava access token for the given user, refreshing if needed.
    Uses strava.py's credential/token infrastructure so per-user DB credentials work."""
    try:
        from app.routers import strava as strava_mod
        tokens = strava_mod.load_tokens(user_id)
        if not tokens:
            return None
        if strava_mod.tokens_are_fresh(tokens):
            return tokens["access_token"]
        # Token expired — refresh using strava.py's credential lookup
        refreshed = await strava_mod.refresh_tokens(tokens, user_id)
        if refreshed:
            strava_mod.save_tokens(refreshed, user_id)
            return refreshed["access_token"]
    except Exception as e:
        log.warning(f"_fresh_token failed for user {user_id}: {e}")
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

def _safe_title(name: str) -> str:
    """Return a filesystem-safe version of an activity name for use in download filenames."""
    import re
    safe = re.sub(r'[^\w\s\-]', '', name).strip()
    safe = re.sub(r'\s+', '_', safe)
    return safe[:60] or "activity"


def _get_info(activity_id: int) -> Optional[dict]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT strava_activity_id, local_media_items_json, local_video_urls_json, user_id, name, attributes_json "
            "FROM activities WHERE id=?",
            (activity_id,)).fetchone()
        if not row:
            return None
        filenames = []
        if row[1]:
            try: filenames = json.loads(row[1])
            except Exception: pass
        video_map = {}
        if row[2]:
            try:
                parsed = json.loads(row[2])
                # Support both new dict format and legacy list format
                if isinstance(parsed, dict):
                    video_map = parsed
                elif isinstance(parsed, list) and parsed:
                    # Legacy list: last N filenames correspond to the N video URLs
                    n = len(parsed)
                    for fname, url in zip(filenames[-n:], parsed):
                        if url:
                            video_map[fname] = url
            except Exception: pass
        # Extract activity name from attributes_json (flat key/value array) or name column
        activity_name = None
        if row[5]:
            try:
                data = json.loads(row[5])
                if isinstance(data, list) and len(data) >= 2:
                    attrs = dict(zip(data[::2], data[1::2]))
                    activity_name = attrs.get("name")
            except Exception: pass
        if not activity_name:
            activity_name = row[4]
        return {"strava_id": row[0], "filenames": filenames, "video_map": video_map, "user_id": row[3],
                "activity_name": activity_name or "activity"}
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
        return {"strava_id": row[0], "filenames": filenames, "video_map": {}, "user_id": None,
                "activity_name": "activity"}
    finally:
        con.close()

def _save_media(activity_id: int, filenames: list[str], video_map: dict):
    """Persist photo filenames and video HLS URL map {filename: hls_url} to the DB."""
    _ensure_video_column()
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "UPDATE activities SET local_media_items_json=?, local_video_urls_json=? WHERE id=?",
            (json.dumps(filenames), json.dumps(video_map), activity_id))
        con.commit()
    finally:
        con.close()

# ── Strava download ───────────────────────────────────────────────────────────

async def _download_from_strava(strava_id: int, dest_dir: Path,
                                 existing: set[str],
                                 user_id: Optional[int] = None) -> tuple[Optional[list], Optional[dict]]:
    """
    Fetch media from Strava for the activity.
    - type=1 (photo): download JPEG, return in filenames list
    - type=2 (video): download thumbnail JPEG, collect HLS URL
    Returns (photo_filenames, video_map_dict), or (None, None) on API failure.
    (None, None) signals a transient error — callers should not wipe existing cached data.
    """
    token = await _fresh_token(user_id)
    if not token:
        log.warning(f"No Strava token for media download (activity {strava_id})")
        return None, None

    filenames = []
    video_map = {}  # {filename: hls_url} for type=2 items with a video URL
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"https://www.strava.com/api/v3/activities/{strava_id}/photos",
                headers={"Authorization": f"Bearer {token}"},
                params={"size": 1024, "photo_sources": "true"},
            )
            if resp.status_code != 200:
                log.warning(f"Strava photos API {resp.status_code} for {strava_id}: {resp.text[:200]}")
                return None, None
            photos = resp.json()
            if not isinstance(photos, list):
                return [], {}

            for i, photo in enumerate(photos):
                media_type = photo.get("type", 1)  # 1=photo, 2=video
                urls       = photo.get("urls") or {}
                thumb_url  = (urls.get("1024") or urls.get("600") or
                              urls.get("2048") or urls.get("100") or
                              photo.get("source_url") or photo.get("url"))
                uid = photo.get("unique_id") or photo.get("id") or i

                if media_type == 2:
                    # Video: collect HLS URL, download thumbnail for panel preview
                    hls = (photo.get("video_url") or photo.get("hls_url") or
                           photo.get("video_hls_url"))
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
                        if hls:
                            video_map[filename] = hls
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
        return None, None

    return filenames, video_map

# ── core resolution ───────────────────────────────────────────────────────────

async def resolve_photos(activity_id: int, force: bool = False) -> dict:
    """
    Ensure all photos for an activity are in support/photos/{strava_id}/.
    force=True: always re-fetch from Strava, replacing any cached media.
    Returns {"filenames": [...], "video_urls": [...]}.
    """
    info = _get_info(activity_id)
    if not info or not info["strava_id"]:
        return {"filenames": [], "video_map": {}}

    strava_id    = int(info["strava_id"])
    db_filenames = info["filenames"]
    db_video_map = info["video_map"]
    user_id      = info.get("user_id")
    dest_dir     = _photos_dir(strava_id)
    legacy_dirs  = _legacy_dirs()

    if force:
        # Always re-download from Strava, discarding cached filenames
        existing_names = {f.stem for f in dest_dir.iterdir() if f.is_file()}
        new_filenames, new_video_map = await _download_from_strava(strava_id, dest_dir, existing_names, user_id)
        if new_filenames is None:
            # Strava API failed — keep existing DB data rather than wiping it
            return {"filenames": db_filenames, "video_map": db_video_map}
        _save_media(activity_id, new_filenames, new_video_map)
        return {"filenames": new_filenames, "video_map": new_video_map}

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
    new_video_map = {}
    strava_failed = False
    if remaining or not db_filenames:
        existing_names = {f.stem for f in dest_dir.iterdir() if f.is_file()}
        dl_filenames, dl_video_map = await _download_from_strava(strava_id, dest_dir, existing_names, user_id)
        if dl_filenames is None:
            strava_failed = True
        else:
            new_filenames = dl_filenames
            new_video_map = dl_video_map
            for fname in new_filenames:
                if fname not in resolved:
                    resolved.append(fname)

    # If Strava failed and we have nothing, scan disk for any existing thumbnails
    if strava_failed and not resolved and not db_filenames:
        disk_files = sorted(f.name for f in dest_dir.iterdir() if f.is_file())
        if disk_files:
            log.warning(f"Strava unavailable; serving {len(disk_files)} cached files from disk for activity {activity_id}")
            return {"filenames": disk_files, "video_map": db_video_map}
        return {"filenames": [], "video_map": {}}

    # If Strava failed but we have existing data, return it unchanged
    if strava_failed:
        return {"filenames": db_filenames or list(resolved), "video_map": db_video_map}

    # Step 5: persist if anything changed
    db_set        = set(db_filenames)
    final         = [f for f in db_filenames if f in resolved]
    for fname in new_filenames:
        if fname not in db_set:
            final.append(fname)

    final_video_map = new_video_map if new_video_map else db_video_map

    if set(final) != set(db_filenames) or final != db_filenames or final_video_map != db_video_map:
        _save_media(activity_id, final, final_video_map)

    return {"filenames": final, "video_map": final_video_map}

# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/activities/{activity_id}/photos")
async def get_photos(activity_id: int):
    """
    Return available photos and video HLS URLs for an activity.
    Response includes a `media` array with type info for the frontend.
    """
    result    = await resolve_photos(activity_id)
    filenames = result["filenames"]
    video_map = result.get("video_map") or {}
    if not isinstance(video_map, dict):
        video_map = {}
    base_url  = f"/photos/{activity_id}/"

    media = []
    for fname in filenames:
        hls = video_map.get(fname)
        if hls:
            media.append({"url": base_url + fname, "type": "video", "hls_url": hls})
        else:
            media.append({"url": base_url + fname, "type": "image"})

    return {
        "photos":     filenames,   # backward compat
        "base_url":   base_url,
        "media":      media,
        "video_urls": list(video_map.values()),
    }


@router.get("/photos/{activity_id}/{filename}/download")
async def download_media(activity_id: int, filename: str):
    """
    Download endpoint for photos and videos.
    - Images: served with Content-Disposition: attachment.
    - Videos: HLS segments are fetched from CDN and streamed back as concatenated MPEG-TS.
    """
    info = _get_info(activity_id)
    if not info or not info["strava_id"]:
        raise HTTPException(404, "Activity not found")

    video_map = info.get("video_map") or {}
    hls_url   = video_map.get(filename)

    if not hls_url:
        # Photo — serve locally with attachment header
        dest_dir  = _photos_dir(info["strava_id"])
        file_path = dest_dir / filename
        try:
            file_path.resolve().relative_to(dest_dir.resolve())
        except ValueError:
            raise HTTPException(403, "Forbidden")
        if not file_path.exists():
            raise HTTPException(404, f"Photo not found: {filename}")
        fn = filename.lower()
        if   fn.endswith(".png"):  mt = "image/png"
        elif fn.endswith(".webp"): mt = "image/webp"
        elif fn.endswith(".heic"): mt = "image/heic"
        else:                      mt = "image/jpeg"
        ext = ('.' + filename.rsplit('.', 1)[1]) if '.' in filename else ''
        dl_name = f"{_safe_title(info['activity_name'])}{ext}"
        return FileResponse(file_path, media_type=mt,
                            headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})

    # Video — fetch HLS from CDN and stream back as MPEG-TS
    async def _stream_hls():
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(hls_url)
            if r.status_code != 200:
                log.warning(f"HLS manifest fetch failed {r.status_code}: {hls_url[:80]}")
                return
            manifest  = r.text
            base_url  = hls_url.rsplit('/', 1)[0] + '/'

            # If this is a master playlist, pick the first (highest-quality) variant
            if '#EXT-X-STREAM-INF' in manifest:
                for line in manifest.splitlines():
                    line = line.strip()
                    if line and not line.startswith('#'):
                        variant_url = line if line.startswith('http') else base_url + line
                        r2 = await client.get(variant_url)
                        if r2.status_code == 200:
                            manifest = r2.text
                            base_url = variant_url.rsplit('/', 1)[0] + '/'
                        break

            # Stream each segment
            for line in manifest.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                seg_url = line if line.startswith('http') else base_url + line
                try:
                    seg = await client.get(seg_url, timeout=30)
                    if seg.status_code == 200:
                        yield seg.content
                except Exception as e:
                    log.warning(f"Segment download failed: {e}")

    dl_name  = f"{_safe_title(info['activity_name'])}.ts"
    return StreamingResponse(
        _stream_hls(),
        media_type="video/mp2t",
        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'},
    )


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
