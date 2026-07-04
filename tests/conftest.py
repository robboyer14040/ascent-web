"""Shared pytest fixtures: a real temporary Ascent DB built by the app's own
create_db(), an authenticated TestClient, and small activity/user factories.

The test DB is created exactly the way production DBs are: create_db() lays down
the base schema, AscentDB.__init__ runs its column migrations + lazy tables, and
we apply the one multi-user migration (activities.user_id) that ships as
scripts/migrate_step2.py. That keeps the fixture schema identical to what the
running app queries against.
"""

import sqlite3
import sys
import uuid as _uuid

import pytest

from app.create_db import create_db
from app.db import AscentDB


# ── live external-API tests: opt-in only ──────────────────────────────────────
# Tests marked `@pytest.mark.live` hit real external services (and use real
# credentials). They are skipped by the normal suite and only run on demand:
#     venv_arm64/bin/python -m pytest --run-live tests/test_live_apis.py -v

def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run live external-API tests (network + real credentials)")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits real external APIs; only runs with --run-live")
    config.addinivalue_line(
        "markers", "api(name): friendly name for the live-API progress line")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live API test — pass --run-live to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ── reassuring per-API progress output (live run only) ────────────────────────
# Prints "running <API> ... " before each live test and "passed/failed/skipped"
# after. Writes to the real terminal (sys.__stdout__) so it shows regardless of
# pytest's output capture.

def _live_active(item) -> bool:
    return bool(item.config.getoption("--run-live")) and item.get_closest_marker("live") is not None


def _api_label(item) -> str:
    m = item.get_closest_marker("api")
    label = m.args[0] if (m and m.args) else (getattr(item, "originalname", None) or item.name)
    if getattr(item, "callspec", None) is not None:
        label = f"{label} [{item.callspec.id}]"
    return label


def pytest_runtest_setup(item):
    if _live_active(item):
        sys.__stdout__.write(f"  running {_api_label(item)} ... ")
        sys.__stdout__.flush()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    if not _live_active(item):
        return
    if rep.when == "setup" and rep.skipped:
        sys.__stdout__.write("skipped\n"); sys.__stdout__.flush()
    elif rep.when == "call":
        word = "passed" if rep.passed else ("skipped" if rep.skipped else "FAILED")
        sys.__stdout__.write(word + "\n"); sys.__stdout__.flush()


def pytest_report_teststatus(report, config):
    # Suppress pytest's own per-test dot/char for live tests so our custom
    # progress lines aren't interleaved with dots. Outcomes still count.
    if config.getoption("--run-live") and "live" in report.keywords:
        if report.when == "call":
            cat = "passed" if report.passed else ("skipped" if report.skipped else "failed")
            return cat, "", ""
        if report.skipped:
            return "skipped", "", ""
        return "", "", ""
    return None


def _apply_multiuser_migration(con: sqlite3.Connection):
    """Add activities.user_id — mirrors scripts/migrate_step2.py step 2.

    create_db() writes the single-user base schema; production DBs additionally
    carry this column, so tests need it too. Idempotent."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(activities)").fetchall()]
    if "user_id" not in cols:
        con.execute("ALTER TABLE activities ADD COLUMN user_id INTEGER")
        con.commit()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.ascentdb")


@pytest.fixture
def test_db(db_path):
    create_db(db_path)                 # exercises the real DB-creation code path
    db = AscentDB(db_path)             # runs __init__ migrations + lazy tables
    _apply_multiuser_migration(db._con)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def make_user(test_db):
    """Factory: create a user, return its id. Each call gets a unique email."""
    seq = {"n": 0}

    def _make(email=None, username="Rider", is_admin=False, password_hash="hash"):
        seq["n"] += 1
        email = email or f"rider{seq['n']}@example.com"
        return test_db.create_user(email=email, username=username,
                                   password_hash=password_hash, is_admin=is_admin)

    return _make


@pytest.fixture
def add_activity(test_db):
    """Factory to insert an activity (+ optional points) for a user.

    points: list of (lat, lon) or (lat, lon, alt_ft). Returns the activity id."""
    import json

    def _add(user_id=1, name="Ride", distance_mi=10.0,
             creation_time_s=1_700_000_000, attrs=None,
             start_lat=None, start_lon=None, points=None):
        if points and start_lat is None:
            start_lat, start_lon = points[0][0], points[0][1]
        con = sqlite3.connect(test_db.path, timeout=30)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            cur = con.execute(
                "INSERT INTO activities (uuid, name, creation_time_s, distance_mi, "
                "attributes_json, user_id, start_lat, start_lon, "
                "has_distance_data, points_saved, points_count) "
                "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (str(_uuid.uuid4()).upper(), name, creation_time_s, distance_mi,
                 json.dumps(attrs or []), user_id, start_lat, start_lon,
                 1 if points else 0, len(points) if points else 0),
            )
            aid = cur.lastrowid
            if points:
                rows = []
                for i, p in enumerate(points):
                    alt = p[2] if len(p) > 2 else 0.0
                    rows.append((aid, i, i, p[0], p[1], alt))
                con.executemany(
                    "INSERT INTO points (track_id, wall_clock_delta_s, "
                    "active_time_delta_s, latitude_e7, longitude_e7, orig_altitude_cm) "
                    "VALUES (?,?,?,?,?,?)", rows,
                )
            con.commit()
            return aid
        finally:
            con.close()

    return _add


@pytest.fixture
def client(test_db):
    """FastAPI TestClient wired to the test DB (no live network)."""
    import app.main as main_mod
    main_mod.set_db(test_db)
    from starlette.testclient import TestClient
    with TestClient(main_mod.app) as c:
        yield c


@pytest.fixture
def authed_client(client, make_user):
    """A TestClient carrying a valid session cookie for a freshly created user.
    The user's id is exposed as `client.user_id`."""
    from app.auth import create_session_token, SESSION_COOKIE
    uid = make_user()
    client.cookies.set(SESSION_COOKIE, create_session_token(uid))
    client.user_id = uid
    return client
