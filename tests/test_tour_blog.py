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
    # Clickable location links need LocationSummary loaded (same as tour / tour-share).
    assert "location_summary.js" in r.text


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


# ── media layout (blog-only reorder + hide) ─────────────────────────────────────

def test_media_layout_owner_saves_and_content_reflects(client, test_db, make_user):
    uid = make_user()
    _tid, sid = _make_tour_with_share(test_db.path, uid)
    _as(client, uid)
    order = ["/photos/1/b.jpg", "/photos/1/a.jpg"]
    hidden = ["/photos/1/a.jpg"]
    r = client.put(f"/tours/blog/{TOKEN}/stage/{sid}/media",
                   json={"order": order, "hidden": hidden})
    assert r.status_code == 200

    _anon(client)
    st = client.get(f"/tours/blog/{TOKEN}/content").json()["stages"][0]
    assert st["media_order"] == order
    assert st["media_hidden"] == hidden


def test_media_layout_non_owner_forbidden(client, test_db, make_user):
    owner = make_user()
    other = make_user()
    _tid, sid = _make_tour_with_share(test_db.path, owner)
    _as(client, other)
    r = client.put(f"/tours/blog/{TOKEN}/stage/{sid}/media", json={"order": [], "hidden": []})
    assert r.status_code == 403


def test_media_layout_unknown_stage_404(client, test_db, make_user):
    uid = make_user()
    _make_tour_with_share(test_db.path, uid)
    _as(client, uid)
    r = client.put(f"/tours/blog/{TOKEN}/stage/99999/media", json={"order": [], "hidden": []})
    assert r.status_code == 404


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


def test_elevation_svg_gradient_fill():
    # Fill is now per-segment, slope-colored polygons (green→red), not a flat blue area.
    pts = [[37.1, -3.6, 2000], [37.2, -3.7, 2500], [37.3, -3.8, 1800]]
    svg = elevation_svg(pts, metric=False)
    assert "<polygon" in svg and "rgb(" in svg
    assert "rgba(59,130,246" not in svg          # old flat-blue fill is gone


def test_elevation_svg_empty():
    assert elevation_svg([[37.1, -3.6, 2000]], metric=False) == ""


def test_pdf_template_body_between_map_and_elevation():
    """The single stage description renders below the map and above the elevation
    profile — matching the blog's stage layout."""
    import os
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    tdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "templates")
    env = Environment(loader=FileSystemLoader(tdir), autoescape=select_autoescape(["html"]))
    ctx = {
        "title": "T", "author": "A", "intro_html": "", "cover_uri": None,
        "done": 1, "total": 1, "tot_dist": "1 mi", "tot_climb": "1 ft", "toc": [],
        "stages": [{
            "stage_num": 1, "name": "S1", "date": "2026-01-01", "chips": [],
            "map_uri": "data:image/png;base64,AA", "elev_svg": "<svg id='elev'></svg>",
            "body_html": "<p>A memorable day on the road.</p>", "figs": [],
        }],
    }
    html = env.get_template("tour_blog_pdf.html").render(**ctx)
    assert "A memorable day on the road." in html
    assert 0 < html.index("stage-map") < html.index("A memorable day") < html.index("<svg id='elev'>")


def test_pdf_template_weather_location_before_body():
    """Weather + location render between the map and the stage description."""
    import os
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    tdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app", "templates")
    env = Environment(loader=FileSystemLoader(tdir), autoescape=select_autoescape(["html"]))
    ctx = {
        "title": "T", "author": "A", "intro_html": "", "cover_uri": None,
        "done": 1, "total": 1, "tot_dist": "1 mi", "tot_climb": "1 ft", "toc": [],
        "stages": [{
            "stage_num": 1, "name": "S1", "date": "2026-01-01", "chips": [],
            "map_uri": "data:image/png;base64,AA", "elev_svg": "",
            "location": "Boulder → Nederland", "weather": "Clear · 68 °F",
            "body_html": "<p>The climb story.</p>", "figs": [],
        }],
    }
    html = env.get_template("tour_blog_pdf.html").render(**ctx)
    assert "Boulder" in html and "Nederland" in html and "Clear · 68 °F" in html
    assert html.index("Boulder") < html.index("The climb story.")


def test_pdf_context_includes_weather_location(client, test_db, make_user, monkeypatch):
    """_pdf_context surfaces a completed stage's weather + location (formatted) so the
    book can print them. The activity resolver is stubbed — no network."""
    import sqlite3 as _sql
    import app.routers.tour_blog as tb
    import app.static_map as sm

    uid = make_user()
    tid, sid = _make_tour_with_share(test_db.path, uid)

    # An activity inside the attempt window that matches the single stage.
    con = _sql.connect(test_db.path)
    try:
        import json
        for col in ("local_media_items_json", "local_media_captions_json"):
            try:
                con.execute(f"ALTER TABLE activities ADD COLUMN {col} TEXT")
            except _sql.OperationalError:
                pass
        con.execute(
            "INSERT INTO activities (id, uuid, user_id, creation_time_s, distance_mi, "
            "start_lat, start_lon, attributes_json) VALUES (?,?,?,?,?,?,?,?)",
            (8001, "act-8001", uid, 1767312000, 12.5, 37.1, -3.6,
             json.dumps(["totalClimb", "800"])),
        )
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(sm, "render_route_png", lambda *a, **k: b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(tb, "_weather_location_blocking", lambda aid: {
        "weather": {"description": "Partly cloudy", "avg_temp_f": 70.0,
                    "avg_wind_kph": 16.0, "precip_mm": 0},
        "locations": "Órgiva → Cáñar",
    })

    con = _sql.connect(test_db.path, timeout=30)
    try:
        ctx = tb._pdf_context(con, TOKEN)
    finally:
        con.close()

    st = ctx["stages"][0]
    assert st["location"] == "Órgiva → Cáñar"
    assert st["weather"].startswith("Partly cloudy · 70 °F")
    assert "Wind 10 mph" in st["weather"]      # 16 km/h → mph, imperial default


def test_pdf_stage_media_keeps_video_poster_caption(test_db, monkeypatch):
    """A Strava video item's poster frame + caption must appear in the PDF book
    (regression: videos were skipped, dropping their descriptive text)."""
    import json, os
    from pathlib import Path
    from PIL import Image
    from app.routers.tour_blog import _pdf_stage_media

    monkeypatch.setenv("ASCENT_DB_PATH", test_db.path)
    con = sqlite3.connect(test_db.path)
    try:
        # Ensure the media columns exist on the harness's activities table.
        for col in ("local_media_items_json", "local_media_captions_json", "local_video_urls_json"):
            try:
                con.execute(f"ALTER TABLE activities ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        sid = 99001
        photo, video = "photo.jpg", "clip.jpg"   # both are still-image files on disk
        con.execute(
            "INSERT INTO activities (id, uuid, strava_activity_id, local_media_items_json, "
            "local_media_captions_json, local_video_urls_json) VALUES (?,?,?,?,?,?)",
            (7001, "act-7001-uuid", sid, json.dumps([photo, video]),
             json.dumps({photo: "A still photo", video: "A short clip"}),
             json.dumps({video: "https://example/clip.m3u8"})),
        )
        con.commit()
        # Write real (tiny) JPEGs where _photos_dir will look for them.
        pdir = Path(test_db.path).parent / "support" / "photos" / str(sid)
        pdir.mkdir(parents=True, exist_ok=True)
        for fn in (photo, video):
            Image.new("RGB", (12, 10), (100, 120, 140)).save(pdir / fn, "JPEG")

        figs = _pdf_stage_media(con, 7001)
        caps = [f["caption"] for f in figs]
        assert "A still photo" in caps
        assert "A short clip" in caps            # video poster caption is kept
    finally:
        con.close()


def test_pdf_stage_media_applies_blog_layout(test_db, monkeypatch):
    """Blog-only order/hidden must reorder and drop photos in the PDF book."""
    import json
    from pathlib import Path
    from PIL import Image
    from app.routers.tour_blog import _pdf_stage_media

    monkeypatch.setenv("ASCENT_DB_PATH", test_db.path)
    con = sqlite3.connect(test_db.path)
    try:
        for col in ("local_media_items_json", "local_media_captions_json", "local_video_urls_json"):
            try:
                con.execute(f"ALTER TABLE activities ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        sid = 99002
        a, b, c = "a.jpg", "b.jpg", "c.jpg"
        con.execute(
            "INSERT INTO activities (id, uuid, strava_activity_id, local_media_items_json, "
            "local_media_captions_json, local_video_urls_json) VALUES (?,?,?,?,?,?)",
            (7002, "act-7002-uuid", sid, json.dumps([a, b, c]),
             json.dumps({a: "cap A", b: "cap B", c: "cap C"}), None),
        )
        con.commit()
        pdir = Path(test_db.path).parent / "support" / "photos" / str(sid)
        pdir.mkdir(parents=True, exist_ok=True)
        for fn in (a, b, c):
            Image.new("RGB", (12, 10), (100, 120, 140)).save(pdir / fn, "JPEG")

        base = "/photos/7002/"
        # Hide c; put b before a.
        figs = _pdf_stage_media(con, 7002, order=[base + b, base + a], hidden=[base + c])
        assert [f["caption"] for f in figs] == ["cap B", "cap A"]

        # No layout → natural order, nothing dropped.
        figs = _pdf_stage_media(con, 7002)
        assert [f["caption"] for f in figs] == ["cap A", "cap B", "cap C"]
    finally:
        con.close()


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
