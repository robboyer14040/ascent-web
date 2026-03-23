# ascent.spec — PyInstaller build spec for Ascent.app
#
# Build with:
#   pyinstaller ascent.spec
#
# Output: dist/Ascent.app

import os
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH)
APP_DIR      = PROJECT_ROOT / "app"

# ── Collect data files ────────────────────────────────────────────────────────
datas = [
    # HTML templates
    (str(APP_DIR / "templates"), "templates"),
    # Static assets
    (str(APP_DIR / "static"),    "static"),
    # Include the entire app package as source so it's importable
    # PyInstaller will compile .py → .pyc but this ensures nothing is missed
    (str(APP_DIR / "routers"),   "app/routers"),
    (str(APP_DIR),               "app"),
]

hiddenimports = [
    # uvicorn internals
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # Starlette
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.base",
    "starlette.staticfiles",
    "starlette.templating",
    "starlette.responses",
    "starlette.background",
    "starlette.concurrency",
    # App modules — list explicitly so PyInstaller finds them all
    "app",
    "app.main",
    "app.db",
    "app.create_db",
    "app.strava_importer",
    "app.routers",
    "app.routers.activities",
    "app.routers.api",
    "app.routers.strava",
    "app.routers.photos",
    "app.routers.settings",
    "app.routers.weather",
    "app.routers.coach",
    # Other deps
    "httpx",
    "httpcore",
    "anyio",
    "anyio.abc",
    "anyio._backends._asyncio",
    "jinja2",
    "jinja2.ext",
    "dotenv",
    "multipart",
    "h11",
    "sqlite3",
    "rumps",
    "pydantic",
    "pydantic.v1",
    "email_validator",
]

# ── Collect entire packages (catches dynamic imports) ─────────────────────────
from PyInstaller.utils.hooks import collect_all

for pkg in ["uvicorn", "starlette", "fastapi", "httpx", "anyio", "jinja2"]:
    pkg_datas, pkg_bins, pkg_hidden = collect_all(pkg)
    datas    += pkg_datas
    hiddenimports += pkg_hidden

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(PROJECT_ROOT / "ascent_launcher.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "cv2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ascent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Ascent",
)

app = BUNDLE(
    coll,
    name="Ascent.app",
    icon=None,
    bundle_identifier="com.ascent.app",
    info_plist={
        "CFBundleName":               "Ascent",
        "CFBundleDisplayName":        "Ascent",
        "CFBundleVersion":            "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable":    True,
        "NSAppleEventsUsageDescription": "Ascent uses AppleScript to show a file picker.",
    },
)
