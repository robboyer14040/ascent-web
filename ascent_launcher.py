"""
ascent_launcher.py — macOS launcher for the Ascent .app bundle.

Responsibilities:
  1. Find or ask for the .ascentdb file (native macOS file picker)
  2. Set environment variables
  3. Add bundle path to sys.path so `app` package is importable
  4. Start uvicorn in a background thread (importing app object directly)
  5. Open the browser
  6. Show a menu-bar icon for clean shutdown
"""

import os
import sys
import time
import socket
import threading
import subprocess
from pathlib import Path
from typing import Optional

# ── Early crash log ───────────────────────────────────────────────────────────
# Write all unhandled exceptions to ~/Library/Logs/Ascent.log so Finder-launched
# crashes (which have no terminal) can be diagnosed.
_LOG = Path.home() / "Library" / "Logs" / "Ascent.log"

def _log(msg: str):
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def _excepthook(exc_type, exc_value, exc_tb):
    import traceback
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    _log(f"UNCAUGHT EXCEPTION:\n{tb}")
    # Also try to show a dialog
    try:
        subprocess.run([
            "osascript", "-e",
            f'display alert "Ascent — Startup Error" message "{str(exc_value)[:200]}" '
            f'buttons {{"OK"}} default button "OK"'
        ], timeout=30)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _excepthook
_log("=== Ascent starting ===")


# ── PyInstaller resource helper ───────────────────────────────────────────────

def bundle_dir() -> Path:
    """Return the directory containing bundled resources."""
    if getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def ensure_bundle_on_path():
    """Make the bundle directory importable so `import app` works."""
    bd = str(bundle_dir())
    if bd not in sys.path:
        sys.path.insert(0, bd)


# ── Persistent config (~/.ascent_config) ─────────────────────────────────────

CONFIG_FILE = Path.home() / ".ascent_config"

def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                cfg[k.strip()] = v.strip()
    return cfg

def save_config(cfg: dict):
    lines = [f"{k}={v}" for k, v in cfg.items()]
    CONFIG_FILE.write_text("\n".join(lines) + "\n")


# ── Native macOS file picker ──────────────────────────────────────────────────

def pick_ascentdb() -> Optional[str]:
    script = '''
        tell application "System Events"
            activate
        end tell
        set f to choose file with prompt "Choose your Ascent database file:" of type {"ascentdb"} with invisibles
        return POSIX path of f
    '''
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=120)
        path = result.stdout.strip()
        return path if path else None
    except Exception:
        return None


# ── Database setup dialogs ───────────────────────────────────────────────────

def prompt_db_choice() -> Optional[str]:
    """
    Show a dialog asking the user whether to create a new database or open
    an existing one. Returns 'new', 'existing', or None (cancelled).
    """
    script = """
        set btn to button returned of (display dialog "Welcome to Ascent!

Do you have an existing Ascent database file, or would you like to create a new one?" ¬
            with title "Ascent — Database Setup" ¬
            buttons {"Quit", "Open Existing…", "Create New…"} ¬
            default button "Create New…" ¬
            with icon note)
        return btn
    """
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=120)
        btn = result.stdout.strip()
        if btn == "Create New…":
            return "new"
        elif btn == "Open Existing…":
            return "existing"
        return None
    except Exception:
        return None


def pick_new_db_location() -> Optional[str]:
    """Show a Save dialog so the user can choose where to create a new .ascentdb file."""
    script = """
        set f to choose file name ¬
            with prompt "Choose a location and name for your new Ascent database:" ¬
            default name "Ascent.ascentdb"
        return POSIX path of f
    """
    try:
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True, timeout=120)
        path = result.stdout.strip()
        if not path:
            return None
        # Ensure .ascentdb extension
        if not path.endswith(".ascentdb"):
            path = path + ".ascentdb"
        return path
    except Exception:
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def find_free_port(preferred: int = 8000) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def alert(title: str, message: str):
    subprocess.run([
        "osascript", "-e",
        f'display alert "{title}" message "{message}" buttons {{"OK"}} default button "OK"'
    ])


# ── Menu bar ──────────────────────────────────────────────────────────────────

def run_menubar(port: int, db_path: str):
    try:
        import rumps

        class AscentApp(rumps.App):
            def __init__(self, port, db_path):
                super().__init__("⛰", quit_button=None)
                self.port    = port
                self.db_path = db_path
                self.menu    = [
                    rumps.MenuItem("Open Ascent",      callback=self.open_browser),
                    rumps.MenuItem("Change Database…", callback=self.change_db),
                    None,
                    rumps.MenuItem("Quit Ascent",      callback=self.quit_app),
                ]

            def open_browser(self, _):
                url = f"http://127.0.0.1:{self.port}"
                subprocess.Popen(["osascript", "-e", f'open location "{url}"'])

            def change_db(self, _):
                new_path = pick_ascentdb()
                if new_path:
                    cfg = load_config()
                    cfg["ASCENT_DB_PATH"] = new_path
                    save_config(cfg)
                    rumps.alert(title="Database changed",
                                message=f"Switched to:\n{new_path}\n\nRestart Ascent to apply.",
                                ok="OK")

            def quit_app(self, _):
                rumps.quit_application()

        AscentApp(port, db_path).run()

    except ImportError:
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # STEP 1: Add bundle dir to sys.path FIRST so all app imports work
    ensure_bundle_on_path()
    _log(f"STEP1 done — bundle_dir={bundle_dir()}, sys.path[0]={sys.path[0]}")

    # STEP 2: Load config
    cfg = load_config()
    _log(f"STEP2 done — config keys: {list(cfg.keys())}")

    # STEP 3: Get database path
    db_path = cfg.get("ASCENT_DB_PATH", "")
    _log(f"STEP3 — db_path from config: '{db_path}'")
    if not db_path or not Path(db_path).exists():
        choice = prompt_db_choice()

        if choice is None:
            # User clicked Quit
            sys.exit(0)

        elif choice == "new":
            # Ask where to save the new file
            new_path = pick_new_db_location()
            if not new_path:
                sys.exit(0)
            # Create the database
            ensure_bundle_on_path()
            from app.create_db import create_db
            create_db(new_path)
            db_path = new_path

        elif choice == "existing":
            chosen = pick_ascentdb()
            if not chosen:
                sys.exit(0)
            db_path = chosen

        cfg["ASCENT_DB_PATH"] = db_path
        save_config(cfg)

    # STEP 4: Set all env vars before importing app
    os.environ["ASCENT_DB_PATH"] = db_path
    bd = bundle_dir()
    os.environ["ASCENT_TEMPLATE_DIR"] = str(bd / "templates")
    os.environ["ASCENT_STATIC_DIR"]   = str(bd / "static")
    for key in ("ANTHROPIC_API_KEY", "STRAVA_CLIENT_ID", "STRAVA_CLIENT_SECRET"):
        if key in cfg:
            os.environ[key] = cfg[key]

    # STEP 5: Free port
    port = find_free_port(8000)
    _log(f"STEP5 — port={port}")

    # STEP 6: Start server — import app object directly, NOT via string
    # String-based "app.main:app" fails in PyInstaller bundles
    server_error = []

    def run_server():
        try:
            import uvicorn
            from app.main import app as fastapi_app
            uvicorn.run(fastapi_app, host="127.0.0.1", port=port, log_level="error")
        except Exception as e:
            server_error.append(str(e))

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Give it a moment to fail fast if there's an import error
    time.sleep(1.0)
    if server_error:
        _log(f"STEP6 — server error: {server_error[0]}")
        alert("Ascent — Server Error", server_error[0][:300])
        sys.exit(1)
    _log("STEP6 — server thread started, waiting...")

    # STEP 7: Wait for server to be ready
    if not wait_for_server(port, timeout=20):
        err = server_error[0][:300] if server_error else "Server did not respond within 20 seconds."
        alert("Ascent — Server Failed to Start", err)
        sys.exit(1)

    # STEP 8: Open browser — use osascript instead of webbrowser.open()
    # webbrowser.open() is unreliable when launched from Finder (no shell env)
    _log(f"STEP8 — opening browser at port {port}")
    url = f"http://127.0.0.1:{port}"
    subprocess.Popen(["osascript", "-e", f'open location "{url}"'])

    # Hide from Dock now that we're running (can't do this before the event
    # loop starts, so we do it here after the browser is open)
    try:
        subprocess.Popen([
            "osascript", "-e",
            'tell application "System Events" to set visible of process "Ascent" to false'
        ])
    except Exception:
        pass

    # STEP 9: Run menu bar (blocks until quit)
    run_menubar(port, db_path)


if __name__ == "__main__":
    main()
