"""routers/settings.py — Database management endpoints."""

import os
import getpass
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

router = APIRouter()
db_getter: Callable = None
db_setter: Callable = None  # function to swap the active DB instance
templates = None


class DbPathRequest(BaseModel):
    path: str


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request=None):
    from fastapi import Request
    db = db_getter()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "db_path":       db.path,
        "activity_count": db.count_activities(),
        "username":      getpass.getuser(),
    })


@router.post("/api/db/create")
async def create_database(req: DbPathRequest):
    """Create a fresh empty Ascent database and switch the app to use it."""
    path = Path(req.path.strip()).expanduser().resolve()

    # Must end in .ascentdb
    if path.suffix != ".ascentdb":
        raise HTTPException(400, "File must have .ascentdb extension")

    # Parent dir must exist
    if not path.parent.exists():
        raise HTTPException(400, f"Directory does not exist: {path.parent}")

    if path.exists():
        raise HTTPException(400, f"File already exists: {path}. Choose a different name.")

    try:
        from app.create_db import create_db
        create_db(str(path))
    except Exception as e:
        raise HTTPException(500, f"Failed to create database: {e}")

    # Switch the running app to the new DB
    _switch_to(str(path))

    return {"status": "ok", "path": str(path)}


@router.post("/api/db/switch")
async def switch_database(req: DbPathRequest):
    """Switch the app to use an existing Ascent database file."""
    path = Path(req.path.strip()).expanduser().resolve()

    if not path.exists():
        raise HTTPException(404, f"File not found: {path}")
    if path.suffix != ".ascentdb":
        raise HTTPException(400, "File must have .ascentdb extension")

    try:
        _switch_to(str(path))
    except Exception as e:
        raise HTTPException(500, f"Failed to switch database: {e}")

    db = db_getter()
    return {"status": "ok", "path": str(path), "activity_count": db.count_activities()}


def _switch_to(path: str):
    """Hot-swap the active database by updating .env and reinitialising AscentDB."""
    from app.db import AscentDB

    # Update the environment variable
    os.environ["ASCENT_DB_PATH"] = path

    # Update the .env file so it persists across restarts
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("ASCENT_DB_PATH="):
                new_lines.append(f"ASCENT_DB_PATH={path}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"ASCENT_DB_PATH={path}")
        env_file.write_text("\n".join(new_lines) + "\n")

    # Replace the live DB instance
    if db_setter:
        new_db = AscentDB(path)
        db_setter(new_db)
