"""
routers/photos.py — On-demand photo resolution + serving.

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

def _get_info(activity_id: int) -> Optional[dict]:
    con = sqlite3.connect(_db_path())
    try:
        row = con.execute(
            "SELECT strava_activity_id, local_media_items_json FROM activities WHERE id=?",
            (activity_id,)).fetchone()
        if not row:
            return None
        filenames = []
        if row[1]:
            try: filenames = json.loads(row[1])
            except Exception: pass
        return {"strava_id": row[0], "filenames": filenames}
    finally:
        con.close()

def _save_filenames(activity_id: int, filenames: list[str]):
    """Persist the canonical filename list to the DB."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute("UPDATE activities SET local_media_items_json=? WHERE id=?",
                    (json.dumps(filenames), activity_id))
        con.commit()
    finally:
        con.close()

# ── Strava download ───────────────────────────────────────────────────────────

async def _download_from_strava(strava_id: int, dest_dir: Path,
                                 existing: set[str]) -> list[str]:
    """
    Fetch photo metadata from Strava, download any photos not already on disk.
    Returns list of filenames successfully saved.
    """
    token = await _fresh_token()
    if not token:
        log.warning(f"No Strava token available for photo download (activity {strava_id})")
        return []

    saved = []
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            # Get photo list
            resp = await client.get(
                f"https://www.strava.com/api/v3/activities/{strava_id}/photos",
                headers={"Authorization": f"Bearer {token}"},
                params={"size": 1024, "photo_sources": "true"},
            )
            if resp.status_code != 200:
                log.warning(f"Strava photos API returned {resp.status_code} for activity {strava_id}: {resp.text[:200]}")
                return []
            photos = resp.json()
            log.info(f"Strava returned {len(photos) if isinstance(photos, list) else photos} photos for activity {strava_id}")
            if not isinstance(photos, list):
                return []

            for i, photo in enumerate(photos):
                # Pick best URL
                urls   = photo.get("urls") or {}
                url    = (urls.get("1024") or urls.get("600") or
                          urls.get("2048") or urls.get("100") or
                          photo.get("source_url") or photo.get("url"))
                if not url:
                    continue

                # Build filename from unique_id or index
                uid = photo.get("unique_id") or photo.get("id") or i
                ext = ".jpg"
                for e in [".png", ".webp", ".heic"]:
                    if e in url.lower():
                        ext = e; break
                filename = f"strava_{uid}{ext}"

                if filename in existing or (dest_dir / filename).exists():
                    saved.append(filename)
                    continue

                try:
                    r = await client.get(url, timeout=30, follow_redirects=True)
                    if r.status_code == 200:
                        (dest_dir / filename).write_bytes(r.content)
                        saved.append(filename)
                    else:
                        log.warning(f"Photo download failed {r.status_code}: {url[:80]}")
                except Exception as e:
                    log.warning(f"Photo download exception: {e}: {url[:80]}")

    except Exception as e:
        log.error(f"_download_from_strava outer exception for {strava_id}: {e}")

    return saved

# ── core resolution ───────────────────────────────────────────────────────────

async def resolve_photos(activity_id: int) -> list[str]:
    """
    Ensure all photos for an activity are in support/photos/{strava_id}/.
    Steps:
      1. Check which DB filenames are already on disk
      2. For missing ones, search legacy media dirs → copy → mark resolved
      3. For still-missing ones, download from Strava → save → mark resolved
      4. If Strava has NEW photos not in DB, add them too
      5. Update DB with the final complete filename list
    Returns the final list of available filenames.
    """
    info = _get_info(activity_id)
    if not info or not info["strava_id"]:
        log.debug(f"resolve_photos: activity {activity_id} has no strava_id")
        return []

    strava_id    = int(info["strava_id"])
    db_filenames = info["filenames"]  # what was previously stored
    dest_dir     = _photos_dir(strava_id)
    legacy_dirs  = _legacy_dirs()

    resolved   = []   # files confirmed on disk
    still_need = []   # not yet found

    # ── Step 1: check what's already in place ─────────────────────────────
    for fname in db_filenames:
        if (dest_dir / fname).exists():
            resolved.append(fname)
        else:
            still_need.append(fname)

    # ── Step 2: search legacy media dirs ─────────────────────────────────
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

    # ── Step 3 & 4: download from Strava if files are missing OR db has no filenames ──
    # Also fetch when db_filenames is empty (fresh Strava-imported activities)
    strava_files = []
    if remaining or not db_filenames:
        existing_names = set(f.stem for f in dest_dir.iterdir() if f.is_file())
        strava_files = await _download_from_strava(strava_id, dest_dir, existing_names)
        for fname in strava_files:
            if fname not in resolved:
                resolved.append(fname)

    # ── Step 5: persist to DB if anything changed ─────────────────────────
    # Build final list: keep original DB order, append genuinely new Strava files
    db_set = set(db_filenames)
    final  = [f for f in db_filenames if f in resolved]
    for fname in strava_files:
        if fname not in db_set:
            final.append(fname)

    # Update DB whenever the list differs from what was stored
    if set(final) != set(db_filenames) or final != db_filenames:
        _save_filenames(activity_id, final)

    return final

# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get("/activities/{activity_id}/photos")
async def get_photos(activity_id: int):
    """
    Return available photos for an activity.
    Triggers lazy copy from legacy media folder and/or Strava download.
    """
    available = await resolve_photos(activity_id)
    return {
        "photos":   available,
        "base_url": f"/photos/{activity_id}/",
    }


@router.get("/photos/{activity_id}/{filename}")
async def serve_photo(activity_id: int, filename: str):
    """Serve a photo from support/photos/{strava_id}/."""
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

    # If not on disk yet, try one more resolution pass
    if not file_path.exists():
        await resolve_photos(activity_id)

    if not file_path.exists():
        raise HTTPException(404, f"Photo not found: {filename}")

    media_type = "image/jpeg"
    if filename.lower().endswith(".png"):  media_type = "image/png"
    if filename.lower().endswith(".webp"): media_type = "image/webp"
    if filename.lower().endswith(".heic"): media_type = "image/heic"

    return FileResponse(file_path, media_type=media_type)
