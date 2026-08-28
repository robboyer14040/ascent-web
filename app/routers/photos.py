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

import os, json, shutil, sqlite3, time, uuid
from pathlib import Path
from typing import Optional, List

import httpx
import logging
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
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

def _ensure_captions_column():
    """Add local_media_captions_json column if it doesn't exist yet."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute("ALTER TABLE activities ADD COLUMN local_media_captions_json TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    finally:
        con.close()

def _ensure_locations_column():
    """Add local_media_locations_json column if it doesn't exist yet."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute("ALTER TABLE activities ADD COLUMN local_media_locations_json TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    finally:
        con.close()

_VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.m4v', '.webm'}
_SKIP_IMAGE_EXTS = _VIDEO_EXTS | {'.heic', '.heif'}
UPLOAD_MAX_BYTES  = 100 * 1024 * 1024   # 100 MB per file
DISK_FREE_MIN     = 200 * 1024 * 1024   # refuse uploads if < 200 MB free

def _user_uploads_dir(activity_id: int) -> Path:
    d = Path(_db_path()).parent / "support" / "user_uploads" / str(activity_id)
    d.mkdir(parents=True, exist_ok=True)
    return d

def _ensure_user_media_table():
    con = sqlite3.connect(_db_path())
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS user_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                activity_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                original_name TEXT,
                caption TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        con.commit()
    finally:
        con.close()

def _get_user_media(activity_id: int) -> list:
    _ensure_user_media_table()
    con = sqlite3.connect(_db_path())
    try:
        rows = con.execute(
            "SELECT id, filename, original_name, caption FROM user_media WHERE activity_id=? ORDER BY created_at",
            (activity_id,)).fetchall()
        return [{"id": r[0], "filename": r[1], "original_name": r[2], "caption": r[3]} for r in rows]
    finally:
        con.close()

def _get_activity_owner(activity_id: int) -> Optional[int]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute("SELECT user_id FROM activities WHERE id=?", (activity_id,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()

def _safe_title(name: str) -> str:
    """Return a filesystem-safe version of an activity name for use in download filenames."""
    import re
    safe = re.sub(r'[^\w\s\-]', '', name).strip()
    safe = re.sub(r'\s+', '_', safe)
    return safe[:60] or "activity"


def _get_info(activity_id: int) -> Optional[dict]:
    # Ensure all lazy columns exist before reading so the full SELECT never fails.
    _ensure_captions_column()
    _ensure_locations_column()
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT strava_activity_id, local_media_items_json, local_video_urls_json, user_id, name, attributes_json, local_media_captions_json, local_media_locations_json "
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
        caption_map = None  # None = never fetched; {} = fetched but no captions
        if row[6] is not None:
            try: caption_map = json.loads(row[6])
            except Exception: caption_map = {}
        location_map = None  # None = never fetched; {} = fetched but no locations
        if row[7] is not None:
            try: location_map = json.loads(row[7])
            except Exception: location_map = {}
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
        return {"strava_id": row[0], "filenames": filenames, "video_map": video_map,
                "caption_map": caption_map, "location_map": location_map, "user_id": row[3],
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
        return {"strava_id": row[0], "filenames": filenames, "video_map": {}, "caption_map": None,
                "location_map": None, "user_id": None, "activity_name": "activity"}
    finally:
        con.close()

def _save_media(activity_id: int, filenames: list[str], video_map: dict, caption_map: dict = None, location_map: dict = None):
    """Persist photo filenames, video HLS URL map, captions, and locations to the DB."""
    _ensure_video_column()
    _ensure_captions_column()
    _ensure_locations_column()
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "UPDATE activities SET local_media_items_json=?, local_video_urls_json=?, local_media_captions_json=?, local_media_locations_json=? WHERE id=?",
            (json.dumps(filenames), json.dumps(video_map),
             json.dumps(caption_map or {}), json.dumps(location_map or {}), activity_id))
        con.commit()
    finally:
        con.close()

# ── Strava download ───────────────────────────────────────────────────────────

async def _download_from_strava(strava_id: int, dest_dir: Path,
                                 existing: set[str],
                                 user_id: Optional[int] = None) -> tuple:
    """
    Fetch media from Strava for the activity.
    - type=1 (photo): download JPEG, return in filenames list
    - type=2 (video): download thumbnail JPEG, collect HLS URL
    Returns (photo_filenames, video_map_dict, caption_map_dict, location_map_dict),
    or (None, None, None, None) on API failure.
    (None, ...) signals a transient error — callers should not wipe existing cached data.
    """
    token = await _fresh_token(user_id)
    if not token:
        log.warning(f"No Strava token for media download (activity {strava_id})")
        return None, None, None, None

    filenames = []
    video_map = {}     # {filename: hls_url} for type=2 items with a video URL
    caption_map = {}   # {filename: caption} for items with a non-empty caption
    location_map = {}  # {filename: [lat, lng]} for items with GPS location
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(
                f"https://www.strava.com/api/v3/activities/{strava_id}/photos",
                headers={"Authorization": f"Bearer {token}"},
                params={"size": 1024, "photo_sources": "true"},
            )
            if resp.status_code != 200:
                log.warning(f"Strava photos API {resp.status_code} for {strava_id}: {resp.text[:200]}")
                return None, None, None, None
            photos = resp.json()
            if not isinstance(photos, list):
                return [], {}, {}, {}

            for i, photo in enumerate(photos):
                media_type = photo.get("type", 1)  # 1=photo, 2=video
                urls       = photo.get("urls") or {}
                thumb_url  = (urls.get("1024") or urls.get("600") or
                              urls.get("2048") or urls.get("100") or
                              photo.get("source_url") or photo.get("url"))
                uid      = photo.get("unique_id") or photo.get("id") or i
                caption  = (photo.get("caption") or "").strip()
                location = photo.get("location")  # [lat, lng] or None

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
                        if caption:
                            caption_map[filename] = caption
                        if isinstance(location, list) and len(location) == 2:
                            location_map[filename] = location
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
                        if caption:
                            caption_map[filename] = caption
                        if isinstance(location, list) and len(location) == 2:
                            location_map[filename] = location
                        continue
                    try:
                        r = await client.get(thumb_url, timeout=30, follow_redirects=True)
                        if r.status_code == 200:
                            (dest_dir / filename).write_bytes(r.content)
                            filenames.append(filename)
                            if caption:
                                caption_map[filename] = caption
                            if isinstance(location, list) and len(location) == 2:
                                location_map[filename] = location
                        else:
                            log.warning(f"Photo download failed {r.status_code}: {thumb_url[:80]}")
                    except Exception as e:
                        log.warning(f"Photo download exception: {e}")

    except Exception as e:
        log.error(f"_download_from_strava outer exception for {strava_id}: {e}")
        return None, None, None, None

    return filenames, video_map, caption_map, location_map

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

    strava_id        = int(info["strava_id"])
    db_filenames     = info["filenames"]
    db_video_map     = info["video_map"]
    db_caption_map   = info.get("caption_map")   # None = never fetched
    db_location_map  = info.get("location_map")  # None if key absent or column missing
    user_id          = info.get("user_id")
    dest_dir         = _photos_dir(strava_id)
    legacy_dirs      = _legacy_dirs()
    log.warning(f"[PHOTO_DEBUG] activity={activity_id} strava={strava_id} user={user_id} "
                f"filenames={len(db_filenames)} caption_map={type(db_caption_map).__name__!r} "
                f"location_map={db_location_map!r}")

    if force:
        # Always re-download from Strava, discarding cached filenames
        existing_names = {f.stem for f in dest_dir.iterdir() if f.is_file()}
        new_filenames, new_video_map, new_caption_map, new_location_map = await _download_from_strava(strava_id, dest_dir, existing_names, user_id)
        if new_filenames is None:
            # Strava API failed — keep existing DB data rather than wiping it
            return {"filenames": db_filenames, "video_map": db_video_map, "caption_map": db_caption_map or {}, "location_map": db_location_map}
        _save_media(activity_id, new_filenames, new_video_map, new_caption_map, new_location_map)
        return {"filenames": new_filenames, "video_map": new_video_map, "caption_map": new_caption_map, "location_map": new_location_map}

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

    # Step 3 & 4: download from Strava if files are missing, db has no filenames,
    # captions have never been fetched, or location data is absent/empty.
    # db_location_map falsy covers: None (column missing/NULL) and {} (saved before GPS feature).
    # After a successful fetch with no GPS we save {"_location_checked": True} so future loads
    # skip this block rather than hitting Strava on every page view.
    new_filenames    = []
    new_video_map    = {}
    new_caption_map  = {}
    new_location_map = None  # None = Strava not called this request
    strava_failed    = False
    trigger = bool(remaining or not db_filenames or db_caption_map is None or not db_location_map)
    log.warning(f"[PHOTO_DEBUG] trigger={trigger} remaining={remaining} "
                f"no_filenames={not db_filenames} cap_none={db_caption_map is None} "
                f"loc_falsy={not db_location_map}")
    if trigger:
        existing_names = {f.stem for f in dest_dir.iterdir() if f.is_file()}
        dl_filenames, dl_video_map, dl_caption_map, dl_location_map = await _download_from_strava(strava_id, dest_dir, existing_names, user_id)
        log.warning(f"[PHOTO_DEBUG] strava result: dl_filenames={dl_filenames!r} dl_location={dl_location_map!r}")
        if dl_filenames is None:
            strava_failed = True
        else:
            new_filenames    = dl_filenames
            new_video_map    = dl_video_map
            new_caption_map  = dl_caption_map
            # If Strava returned no GPS data, store a sentinel so we don't re-fetch
            # on every subsequent page load for photos that have no GPS.
            new_location_map = dl_location_map if dl_location_map else {"_location_checked": True}
            for fname in new_filenames:
                if fname not in resolved:
                    resolved.append(fname)

    # If Strava failed and we have nothing, scan disk for any existing thumbnails
    if strava_failed and not resolved and not db_filenames:
        disk_files = sorted(f.name for f in dest_dir.iterdir() if f.is_file())
        if disk_files:
            log.warning(f"Strava unavailable; serving {len(disk_files)} cached files from disk for activity {activity_id}")
            return {"filenames": disk_files, "video_map": db_video_map, "caption_map": db_caption_map or {}, "location_map": db_location_map}
        return {"filenames": [], "video_map": {}, "caption_map": {}, "location_map": {}}

    # If Strava failed but we have existing data, return it unchanged
    if strava_failed:
        return {"filenames": db_filenames or list(resolved), "video_map": db_video_map, "caption_map": db_caption_map or {}, "location_map": db_location_map}

    # Step 5: persist if anything changed
    db_set        = set(db_filenames)
    final         = [f for f in db_filenames if f in resolved]
    for fname in new_filenames:
        if fname not in db_set:
            final.append(fname)

    final_video_map    = new_video_map if new_video_map else db_video_map
    final_caption_map  = new_caption_map if new_caption_map else (db_caption_map or {})
    final_location_map = new_location_map if new_location_map is not None else (db_location_map or {})

    if set(final) != set(db_filenames) or final != db_filenames or final_video_map != db_video_map or final_caption_map != (db_caption_map or {}) or final_location_map != db_location_map:
        _save_media(activity_id, final, final_video_map, final_caption_map, final_location_map)

    return {"filenames": final, "video_map": final_video_map, "caption_map": final_caption_map, "location_map": final_location_map}

# ── API endpoints ──────────────────────────────────────────────────────────────

def first_local_image(activity_id: int) -> Optional[Path]:
    """Path of the activity's first still image already on disk, or None.

    Deliberately disk-only: resolve_photos() will download anything missing from
    Strava, and callers that must not block on the network (the stage-update
    email) would rather send without a photo than wait for one. HEIC is skipped
    because Pillow cannot decode it without a plugin, and mail clients mostly
    cannot render it either.
    """
    info = _get_info(activity_id)
    if info and info.get("strava_id"):
        video_map = info.get("video_map") or {}
        dest_dir  = _photos_dir(int(info["strava_id"]))
        for fname in info["filenames"]:
            if fname in video_map or Path(fname).suffix.lower() in _SKIP_IMAGE_EXTS:
                continue
            path = dest_dir / fname
            if path.exists():
                return path
    for media in _get_user_media(activity_id):
        fname = media["filename"]
        if Path(fname).suffix.lower() in _SKIP_IMAGE_EXTS:
            continue
        path = _user_uploads_dir(activity_id) / fname
        if path.exists():
            return path
    return None


@router.get("/activities/{activity_id}/photos")
async def get_photos(activity_id: int):
    """
    Return available photos and video HLS URLs for an activity.
    Response includes a `media` array with type info for the frontend.
    """
    result       = await resolve_photos(activity_id)
    filenames    = result["filenames"]
    video_map    = result.get("video_map") or {}
    caption_map  = result.get("caption_map") or {}
    location_map = result.get("location_map") or {}
    if not isinstance(video_map, dict):
        video_map = {}
    if not isinstance(caption_map, dict):
        caption_map = {}
    if not isinstance(location_map, dict):
        location_map = {}
    base_url  = f"/photos/{activity_id}/"

    media = []
    for fname in filenames:
        hls      = video_map.get(fname)
        caption  = caption_map.get(fname) or ""
        location = location_map.get(fname)
        item     = {"url": base_url + fname, "type": "video" if hls else "image"}
        if hls:
            item["hls_url"] = hls
        if caption:
            item["caption"] = caption
        if isinstance(location, list) and len(location) == 2:
            item["location"] = location
        media.append(item)

    # Merge user-uploaded media (not tied to Strava)
    for um in _get_user_media(activity_id):
        fn  = um["filename"]
        ext = Path(fn).suffix.lower()
        item: dict = {"url": f"/user-uploads/{activity_id}/{fn}",
                      "type": "video" if ext in _VIDEO_EXTS else "image",
                      "user_upload": True}
        if um.get("caption"):
            item["caption"] = um["caption"]
        media.append(item)

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


# ── User upload endpoints ──────────────────────────────────────────────────────

def _media_type_for(filename: str) -> str:
    fn = filename.lower()
    if fn.endswith(".mp4"):  return "video/mp4"
    if fn.endswith(".mov"):  return "video/quicktime"
    if fn.endswith(".avi"):  return "video/x-msvideo"
    if fn.endswith(".mkv"):  return "video/x-matroska"
    if fn.endswith(".webm"): return "video/webm"
    if fn.endswith(".png"):  return "image/png"
    if fn.endswith(".webp"): return "image/webp"
    if fn.endswith(".heic"): return "image/heic"
    return "image/jpeg"


@router.post("/activities/{activity_id}/upload-media")
async def upload_media(request: Request, activity_id: int,
                       files: List[UploadFile] = File(...)):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        raise HTTPException(401, "Not authenticated")

    owner_id = _get_activity_owner(activity_id)
    if owner_id is None:
        raise HTTPException(404, "Activity not found")
    if owner_id != uid:
        raise HTTPException(403, "Not your activity")

    # Check available disk space
    try:
        import shutil as _shutil
        free = _shutil.disk_usage(Path(_db_path()).parent).free
        if free < DISK_FREE_MIN:
            raise HTTPException(507, "Insufficient storage — please contact the administrator")
    except HTTPException:
        raise
    except Exception:
        pass  # can't check, allow the upload

    dest_dir = _user_uploads_dir(activity_id)
    _ensure_user_media_table()
    saved = []

    for file in files:
        content = await file.read(UPLOAD_MAX_BYTES + 1)
        if len(content) > UPLOAD_MAX_BYTES:
            raise HTTPException(413, f"{file.filename!r} exceeds the 100 MB size limit")

        orig = file.filename or "upload"
        ext  = Path(orig).suffix.lower() or ".jpg"
        filename = f"user_{uuid.uuid4().hex[:16]}{ext}"

        (dest_dir / filename).write_bytes(content)

        con = sqlite3.connect(_db_path())
        try:
            cur = con.execute(
                "INSERT INTO user_media (user_id, activity_id, filename, original_name) VALUES (?,?,?,?)",
                (uid, activity_id, filename, orig))
            media_id = cur.lastrowid
            con.commit()
        finally:
            con.close()

        saved.append({"id": media_id, "filename": filename,
                      "url": f"/user-uploads/{activity_id}/{filename}"})

    return {"saved": saved}


@router.get("/user-uploads/{activity_id}/{filename}/download")
async def download_user_upload(activity_id: int, filename: str):
    dest_dir  = _user_uploads_dir(activity_id)
    file_path = dest_dir / filename
    try:
        file_path.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Forbidden")
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    info    = _get_info(activity_id)
    name    = info["activity_name"] if info else "activity"
    ext     = ('.' + filename.rsplit('.', 1)[1]) if '.' in filename else ''
    dl_name = f"{_safe_title(name)}{ext}"
    return FileResponse(file_path, media_type=_media_type_for(filename),
                        headers={"Content-Disposition": f'attachment; filename="{dl_name}"'})


@router.post("/activities/{activity_id}/set-media-caption")
async def set_media_caption(request: Request, activity_id: int):
    from app.auth import get_session_user_id
    uid = get_session_user_id(request)
    if not uid:
        raise HTTPException(401, "Not authenticated")

    body        = await request.json()
    filename    = (body.get("filename") or "").strip()
    caption     = (body.get("caption") or "").strip()
    user_upload = bool(body.get("user_upload", False))

    if not filename:
        raise HTTPException(400, "filename required")

    if user_upload:
        _ensure_user_media_table()
        con = sqlite3.connect(_db_path())
        try:
            row = con.execute(
                "SELECT user_id FROM user_media WHERE activity_id=? AND filename=?",
                (activity_id, filename)).fetchone()
            if not row:
                raise HTTPException(404, "Media not found")
            if row[0] != uid:
                raise HTTPException(403, "Not your media")
            con.execute(
                "UPDATE user_media SET caption=? WHERE activity_id=? AND filename=?",
                (caption or None, activity_id, filename))
            con.commit()
        finally:
            con.close()
    else:
        owner_id = _get_activity_owner(activity_id)
        if owner_id is None:
            raise HTTPException(404, "Activity not found")
        if owner_id != uid:
            raise HTTPException(403, "Not your activity")
        _ensure_captions_column()
        con = sqlite3.connect(_db_path())
        try:
            row = con.execute(
                "SELECT local_media_captions_json FROM activities WHERE id=?",
                (activity_id,)).fetchone()
            caption_map: dict = {}
            if row and row[0]:
                try:
                    caption_map = json.loads(row[0])
                except Exception:
                    pass
            if caption:
                caption_map[filename] = caption
            else:
                caption_map.pop(filename, None)
            con.execute(
                "UPDATE activities SET local_media_captions_json=? WHERE id=?",
                (json.dumps(caption_map), activity_id))
            con.commit()
        finally:
            con.close()

    return {"ok": True}


@router.get("/user-uploads/{activity_id}/{filename}")
async def serve_user_upload(activity_id: int, filename: str):
    dest_dir  = _user_uploads_dir(activity_id)
    file_path = dest_dir / filename
    try:
        file_path.resolve().relative_to(dest_dir.resolve())
    except ValueError:
        raise HTTPException(403, "Forbidden")
    if not file_path.exists():
        raise HTTPException(404, f"File not found: {filename}")
    return FileResponse(file_path, media_type=_media_type_for(filename))
