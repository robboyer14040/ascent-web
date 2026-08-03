"""Tests for the tour-blog feature: content JSON, owner-only editing guards,
open comments with honeypot + moderation, and the PDF helpers.

Follows the shared harness in conftest.py (real temp DB, TestClient). Tour tables
are created via the routers' own _ensure_tables; a tour + share token is inserted
directly so tests don't need the GPX-upload flow.
"""

import sqlite3
import time

from app.auth import create_session_token, SESSION_COOKIE
from app.routers.tours import _ensure_tables
from app.routers.tour_blog import _ensure_blog_tables, render_md
from app.static_map import elevation_svg, render_route_png


TOKEN = "tok_blog_test_0001"


def _make_tour_with_share(db_path, owner_uid, token=TOKEN):
    """Insert a tour + one stage (with route points) + attempt + share token."""
    con = sqlite3.connect(db_path, timeout=30)
    try:
        _ensure_tables(con)
        _ensure_blog_tables(con)
        cur = con.execute(
            "INSERT INTO tours (created_by, title, start_date, end_date, created_at, shared) "
            "VALUES (?,?,?,?,?,1)", (owner_uid, "Test Tour", "", "", int(time.time())),
        )
        tid = cur.lastrowid
        cur = con.execute(
            "INSERT INTO tour_stages (tour_id, stage_num, name, distance_mi, climb_ft) "
            "VALUES (?,?,?,?,?)", (tid, 1, "Stage One", 12.5, 800),
        )
        sid = cur.lastrowid
        pts = [(37.1 + i * 0.001, -3.6 + i * 0.001, 2000 + i * 10) for i in range(10)]
        con.executemany(
            "INSERT INTO tour_stage_points (stage_id, seq, lat, lon, alt_ft) VALUES (?,?,?,?,?)",
            [(sid, i, p[0], p[1], p[2]) for i, p in enumerate(pts)],
        )
        cur = con.execute(
            "INSERT INTO tour_attempts (tour_id, user_id, start_date, end_date, created_at) "
            "VALUES (?,?,?,?,?)", (tid, owner_uid, "2026-01-01", None, int(time.time())),
        )
        con.execute(
            "INSERT INTO tour_shares (tour_id, user_id, token, attempt_id) VALUES (?,?,?,?)",
            (tid, owner_uid, token, cur.lastrowid),
        )
        con.commit()
        return tid, sid
    finally:
        con.close()


def _as(client, uid):
    client.cookies.set(SESSION_COOKIE, create_session_token(uid))


def _anon(client):
    client.cookies.clear()


# ── render_md ─────────────────────────────────────────────────────────────────

def test_render_md_escapes_and_formats():
    out = render_md("Hi **bold** and *em* [x](https://a.com)\n\nsecond")
    assert "<strong>bold</strong>" in out
    assert "<em>em</em>" in out
    assert '<a href="https://a.com"' in out
    assert out.count("<p>") == 2


def test_render_md_escapes_html():
    out = render_md("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_md_rejects_javascript_url():
    # Only http(s) / root-relative links become anchors.
    assert "<a " not in render_md("[x](javascript:alert(1))")


# ── content ───────────────────────────────────────────────────────────────────

def test_content_public(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    r = client.get(f"/tours/blog/{TOKEN}/content")
    assert r.status_code == 200
    d = r.json()
    assert d["title"] == "Test Tour"
    assert len(d["stages"]) == 1
    assert d["stages"][0]["name"] == "Stage One"


def test_content_bad_token_404(client, test_db):
    assert client.get("/tours/blog/nope/content").status_code == 404


def test_blog_page_renders(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    r = client.get(f"/tours/blog/{TOKEN}")
    assert r.status_code == 200
    assert "window.BLOG" in r.text


# ── owner editing guards ──────────────────────────────────────────────────────

def test_intro_requires_login(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _anon(client)
    r = client.put(f"/tours/blog/{TOKEN}/intro", json={"intro": "hi"})
    assert r.status_code == 401


def test_intro_non_owner_forbidden(client, test_db, make_user):
    owner = make_user()
    other = make_user()
    _make_tour_with_share(test_db.path, owner)
    _as(client, other)
    r = client.put(f"/tours/blog/{TOKEN}/intro", json={"intro": "hi"})
    assert r.status_code == 403


def test_owner_edits_intro_and_stage(client, test_db, make_user):
    uid = make_user()
    _tid, sid = _make_tour_with_share(test_db.path, uid)
    _as(client, uid)
    r = client.put(f"/tours/blog/{TOKEN}/intro", json={"intro": "An **epic** ride."})
    assert r.status_code == 200
    assert "<strong>epic</strong>" in r.json()["intro_html"]

    r = client.put(f"/tours/blog/{TOKEN}/stage/{sid}", json={"body": "Stage *story*."})
    assert r.status_code == 200
    assert "<em>story</em>" in r.json()["body_html"]

    # Persisted: content reflects the edits.
    _anon(client)
    d = client.get(f"/tours/blog/{TOKEN}/content").json()
    assert "epic" in d["intro_html"]
    assert d["stages"][0]["body_raw"] == "Stage *story*."


def test_edit_unknown_stage_404(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _as(client, uid)
    assert client.put(f"/tours/blog/{TOKEN}/stage/99999", json={"body": "x"}).status_code == 404


# ── comments ──────────────────────────────────────────────────────────────────

def test_comment_post_and_list(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _anon(client)
    r = client.post(f"/tours/blog/{TOKEN}/comments",
                    json={"stage_num": 1, "author_name": "Reader", "body": "Nice!", "website": ""})
    assert r.status_code == 200
    c = r.json()["comment"]
    assert c["is_owner"] is False and c["author_name"] == "Reader" and c["stage_num"] == 1

    lst = client.get(f"/tours/blog/{TOKEN}/comments").json()["comments"]
    assert len(lst) == 1 and lst[0]["body"] == "Nice!"


def test_comment_honeypot_rejected(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _anon(client)
    r = client.post(f"/tours/blog/{TOKEN}/comments",
                    json={"body": "spam", "website": "http://spam.example"})
    assert r.status_code == 400
    assert client.get(f"/tours/blog/{TOKEN}/comments").json()["comments"] == []


def test_comment_empty_rejected(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _anon(client)
    assert client.post(f"/tours/blog/{TOKEN}/comments", json={"body": "   "}).status_code == 400


def test_owner_comment_flagged(client, test_db, make_user):
    uid = make_user(username="Captain")
    _make_tour_with_share(test_db.path, uid)
    _as(client, uid)
    c = client.post(f"/tours/blog/{TOKEN}/comments", json={"body": "Thanks all!"}).json()["comment"]
    assert c["is_owner"] is True and c["author_name"] == "Captain"


def test_comment_delete_owner_only(client, test_db, make_user):
    owner = make_user()
    other = make_user()
    _make_tour_with_share(test_db.path, owner)
    _anon(client)
    cid = client.post(f"/tours/blog/{TOKEN}/comments",
                      json={"body": "hello", "website": ""}).json()["comment"]["id"]

    _anon(client)
    assert client.delete(f"/tours/blog/{TOKEN}/comments/{cid}").status_code == 401
    _as(client, other)
    assert client.delete(f"/tours/blog/{TOKEN}/comments/{cid}").status_code == 403
    _as(client, owner)
    assert client.delete(f"/tours/blog/{TOKEN}/comments/{cid}").status_code == 200
    assert client.get(f"/tours/blog/{TOKEN}/comments").json()["comments"] == []


def test_delete_removes_replies(client, test_db, make_user):
    owner = make_user()
    _make_tour_with_share(test_db.path, owner)
    _anon(client)
    parent = client.post(f"/tours/blog/{TOKEN}/comments",
                         json={"body": "top", "website": ""}).json()["comment"]["id"]
    client.post(f"/tours/blog/{TOKEN}/comments",
                json={"body": "reply", "parent_id": parent, "website": ""})
    _as(client, owner)
    client.delete(f"/tours/blog/{TOKEN}/comments/{parent}")
    assert client.get(f"/tours/blog/{TOKEN}/comments").json()["comments"] == []


# ── PDF helpers (pure; no network) ────────────────────────────────────────────

def test_elevation_svg():
    pts = [[37.1, -3.6, 2000], [37.2, -3.7, 2500], [37.3, -3.8, 1800]]
    svg = elevation_svg(pts, metric=False)
    assert svg.startswith("<svg") and "polyline" in svg


def test_elevation_svg_empty():
    assert elevation_svg([[37.1, -3.6, 2000]], metric=False) == ""


def test_render_route_png_degenerate_returns_png():
    # <2 points → background PNG, no network.
    png = render_route_png([(37.1, -3.6)])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def _weasyprint_present():
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False


def test_pdf_route_available_or_501(client, test_db, make_user, monkeypatch):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    # Stub the tile renderer so the suite never hits the network, even where
    # WeasyPrint is installed. _pdf_context imports render_route_png at call time.
    import app.static_map as sm
    monkeypatch.setattr(sm, "render_route_png", lambda *a, **k: b"\x89PNG\r\n\x1a\n")
    # 501 when WeasyPrint isn't installed; 200 application/pdf when it is.
    r = client.get(f"/tours/blog/{TOKEN}/pdf")
    assert r.status_code in (200, 501)
    if r.status_code == 200:
        assert r.headers["content-type"] == "application/pdf"


def test_pdf_job_start_bad_token(client, test_db, make_user):
    make_user()
    r = client.post("/tours/blog/nope/pdf/start")
    # 404 (bad token) when WeasyPrint present, 501 when not — both before any work.
    assert r.status_code in (404, 501)


def test_pdf_job_status_unknown(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    assert client.get(f"/tours/blog/{TOKEN}/pdf/status/deadbeef").status_code == 404


def test_pdf_job_full_flow(client, test_db, make_user, monkeypatch):
    if not _weasyprint_present():
        import pytest
        pytest.skip("WeasyPrint not installed")
    import time as _t
    import app.static_map as sm
    monkeypatch.setattr(sm, "render_route_png", lambda *a, **k: b"\x89PNG\r\n\x1a\n")

    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    job_id = client.post(f"/tours/blog/{TOKEN}/pdf/start").json()["job_id"]

    # Poll until the background thread finishes (bounded).
    status = None
    for _ in range(100):
        status = client.get(f"/tours/blog/{TOKEN}/pdf/status/{job_id}").json()
        if status["status"] in ("ready", "error"):
            break
        _t.sleep(0.05)
    assert status["status"] == "ready", status

    dl = client.get(f"/tours/blog/{TOKEN}/pdf/download/{job_id}")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content[:4] == b"%PDF"
