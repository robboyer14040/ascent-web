"""Database-creation, user CRUD, settings, profile/prefs, and invites — all
against a real temp Ascent DB built by the app's own create_db()."""

import sqlite3


# ── create_db() bootstrap ─────────────────────────────────────────────────────

def test_create_db_builds_core_schema(tmp_path):
    from app.create_db import create_db
    p = str(tmp_path / "fresh.ascentdb")
    create_db(p)
    con = sqlite3.connect(p)
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    meta = con.execute("SELECT id, totalTracks FROM meta").fetchone()
    con.close()
    assert {"activities", "points", "laps", "meta"} <= tables
    assert meta == (1, 0)   # single seed meta row, no tracks yet


def test_fresh_db_opens_empty(test_db):
    assert test_db.count_activities() == 0


# ── user create / get / list ──────────────────────────────────────────────────

def test_create_and_get_user(test_db):
    uid = test_db.create_user(email="Rider@Example.com", username="Rider",
                              password_hash="hash")
    u = test_db.get_user(uid)
    assert u["email"] == "rider@example.com"          # lowercased on write
    assert u["username"] == "Rider"
    assert u["is_admin"] == 0
    assert test_db.get_user_by_email("rider@example.com")["id"] == uid
    assert test_db.get_user_by_username("RIDER")["id"] == uid   # case-insensitive


def test_duplicate_email_rejected(test_db):
    test_db.create_user(email="dup@x.com", username="One")
    with_raises = False
    try:
        test_db.create_user(email="dup@x.com", username="Two")
    except sqlite3.IntegrityError:
        with_raises = True
    assert with_raises, "duplicate email should violate the UNIQUE constraint"


def test_list_users(test_db, make_user):
    a = make_user(username="A")
    b = make_user(username="B", is_admin=True)
    ids = {u["id"] for u in test_db.list_users()}
    assert ids == {a, b}


# ── settings ──────────────────────────────────────────────────────────────────

def test_update_user_settings(test_db, make_user):
    uid = make_user()
    test_db.update_user_settings(uid, share_activities=1, anthropic_api_key="sk-test")
    u = test_db.get_user(uid)
    assert u["share_activities"] == 1
    assert u["anthropic_api_key"] == "sk-test"


def test_update_user_settings_ignores_unlisted_fields(test_db, make_user):
    uid = make_user()
    # is_admin isn't in the allowed set → silently ignored, stays 0.
    test_db.update_user_settings(uid, is_admin=1)
    assert test_db.get_user(uid)["is_admin"] == 0


# ── profile + UI prefs ────────────────────────────────────────────────────────

def test_user_profile_defaults_then_round_trip(test_db, make_user):
    uid = make_user()
    prof0 = test_db.get_user_profile(uid)
    assert prof0["max_hr"] is None and prof0["use_metric"] is False
    test_db.set_user_profile(uid, max_hr=185, ftp_watts=250, age=41, use_metric=True)
    prof = test_db.get_user_profile(uid)
    assert prof["max_hr"] == 185
    assert prof["ftp_watts"] == 250
    assert prof["age"] == 41
    assert prof["use_metric"] is True


def test_ui_prefs_merge_not_overwrite(test_db, make_user):
    uid = make_user()
    assert test_db.get_ui_prefs(uid) == {}
    test_db.set_ui_prefs(uid, {"theme": "dark"})
    test_db.set_ui_prefs(uid, {"units": "mi"})   # should merge with existing
    assert test_db.get_ui_prefs(uid) == {"theme": "dark", "units": "mi"}


# ── delete user (cascades activities + points) ────────────────────────────────

def test_delete_user_removes_user_and_their_data(test_db, make_user, add_activity):
    uid = make_user()
    other = make_user()
    add_activity(user_id=uid, points=[(40.0, -105.0), (40.01, -105.0)])
    add_activity(user_id=other, points=[(41.0, -106.0)])

    summary = test_db.delete_user(uid)

    assert test_db.get_user(uid) is None
    assert summary["activities"] == 1
    assert summary["points"] == 2
    # the other user's data is untouched
    assert test_db.get_user(other) is not None
    con = sqlite3.connect(test_db.path)
    remaining = con.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    con.close()
    assert remaining == 1


# ── invites ───────────────────────────────────────────────────────────────────

def test_invite_lifecycle(test_db, make_user):
    inviter = make_user()
    test_db.create_invite("tok123", email="Friend@X.com", invited_by_user_id=inviter)
    inv = test_db.get_invite("tok123")
    assert inv["email"] == "friend@x.com"
    assert inv["used_at"] is None

    joined = make_user(email="friend@x.com")
    test_db.mark_invite_used("tok123", used_by_user_id=joined)
    assert test_db.get_invite("tok123")["used_by_user_id"] == joined
