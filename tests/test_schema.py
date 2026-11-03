"""The schema a fresh DB gets must be the schema the app queries against.

create_db() is the only code path that builds a new Ascent DB — used by
AscentDB.__init__ for a missing file, by POST /api/db/create, and by the test
harness. It has to lay down everything the running app assumes, otherwise a
brand-new DB is unqueryable and only `scripts/migrate_step2.py` makes it work.
"""

import sqlite3

import pytest

from app.create_db import create_db
from app.db import AscentDB


def _cols(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]


def _indexes(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()]


def test_fresh_db_has_user_id_column(tmp_path):
    """Multi-tenancy scopes every query by user_id, so the column must exist."""
    p = str(tmp_path / "fresh.ascentdb")
    create_db(p)
    con = sqlite3.connect(p)
    try:
        assert "user_id" in _cols(con, "activities")
    finally:
        con.close()


def test_fresh_db_has_the_multiuser_indexes(tmp_path):
    p = str(tmp_path / "fresh.ascentdb")
    create_db(p)
    con = sqlite3.connect(p)
    try:
        names = _indexes(con)
        assert "idx_activities_user_id" in names
        assert "idx_activities_user_bbox" in names
    finally:
        con.close()


def test_fresh_db_answers_a_user_scoped_query(tmp_path):
    """The end-to-end point: a brand-new DB must be queryable as-is, with no
    out-of-band migration step."""
    p = str(tmp_path / "fresh.ascentdb")
    db = AscentDB(p)
    try:
        assert db.get_activities(user_id=1) == []
        assert db.get_dashboard_stats(user_id=1) is not None
    finally:
        db.close()


def test_legacy_db_without_user_id_is_healed_on_open(tmp_path):
    """A pre-multiuser DB opened by the app gains the column rather than
    failing every scoped query."""
    p = str(tmp_path / "legacy.ascentdb")
    create_db(p)
    con = sqlite3.connect(p)
    try:
        # Take the real schema back to its pre-multiuser shape by dropping the
        # column (and the indexes that reference it), so only user_id is absent.
        con.execute("DROP INDEX IF EXISTS idx_activities_user_id")
        con.execute("DROP INDEX IF EXISTS idx_activities_user_bbox")
        con.execute("ALTER TABLE activities DROP COLUMN user_id")
        con.execute("INSERT INTO activities (id, uuid, name, creation_time_s) "
                    "VALUES (1, 'U1', 'Legacy ride', 1700000000)")
        con.commit()
        assert "user_id" not in _cols(con, "activities")
    finally:
        con.close()

    db = AscentDB(p)
    try:
        con2 = sqlite3.connect(p)
        try:
            assert "user_id" in _cols(con2, "activities")
        finally:
            con2.close()
        # The legacy row is unowned, which the app treats as visible.
        assert db.get_activities(user_id=1) is not None
    finally:
        db.close()


def test_create_db_is_idempotent(tmp_path):
    """AscentDB.__init__ calls create_db for a missing file; running the DDL
    twice must not fail."""
    p = str(tmp_path / "twice.ascentdb")
    create_db(p)
    create_db(p)
    con = sqlite3.connect(p)
    try:
        assert con.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    finally:
        con.close()
