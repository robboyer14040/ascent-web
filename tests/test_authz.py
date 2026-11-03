"""Authorization regression tests for the endpoints that were missing an auth check.

Each test pins one rule:
  - anonymous callers are refused (401/403), never served;
  - a logged-in peer cannot reach another user's private data or mutate their rows;
  - the opt-in sharing model (`share_activities` / `share_segments`) still works,
    so closing the holes must not break cross-user compare or the share pages.
"""

import sqlite3

import pytest


def _client_for(uid):
    """A fresh TestClient carrying `uid`'s session cookie — a distinct browser."""
    from starlette.testclient import TestClient
    from app.auth import create_session_token, SESSION_COOKIE
    import app.main as main_mod
    c = TestClient(main_mod.app)
    c.cookies.set(SESSION_COOKIE, create_session_token(uid))
    return c


def _set_sharing(test_db, uid, *, activities=0, segments=0):
    test_db._con.execute(
        "UPDATE users SET share_activities=?, share_segments=? WHERE id=?",
        (activities, segments, uid))
    test_db._con.commit()


TRACK = [(40.0, -105.0, 1600.0), (40.001, -105.001, 1610.0), (40.002, -105.002, 1620.0)]


# ── DELETE /api/activities ────────────────────────────────────────────────────

def test_anon_cannot_delete_activities(client, make_user, add_activity, test_db):
    victim = make_user()
    aid = add_activity(user_id=victim, name="Victim's ride")

    r = client.request("DELETE", "/api/activities", json={"ids": [aid]})
    assert r.status_code == 401, r.text
    assert test_db.get_activity(aid) is not None


def test_peer_cannot_delete_another_users_activities(authed_client, make_user,
                                                     add_activity, test_db):
    victim = make_user()
    aid = add_activity(user_id=victim, name="Victim's ride")

    r = authed_client.request("DELETE", "/api/activities", json={"ids": [aid]})
    assert r.json()["deleted"] == 0
    assert test_db.get_activity(aid) is not None


def test_owner_can_still_delete_own_activities(authed_client, add_activity, test_db):
    aid = add_activity(user_id=authed_client.user_id, name="Mine")

    r = authed_client.request("DELETE", "/api/activities", json={"ids": [aid]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 1
    assert test_db.get_activity(aid) is None


def test_mixed_delete_removes_only_own_activities(authed_client, make_user,
                                                  add_activity, test_db):
    """A batch naming someone else's id deletes only the caller's own rows."""
    mine = add_activity(user_id=authed_client.user_id, name="Mine")
    theirs = add_activity(user_id=make_user(), name="Theirs")

    r = authed_client.request("DELETE", "/api/activities",
                              json={"ids": [mine, theirs]})
    assert r.json()["deleted"] == 1
    assert test_db.get_activity(mine) is None
    assert test_db.get_activity(theirs) is not None


# ── activity read endpoints (charts / geojson / laps) ─────────────────────────

READ_PATHS = ("charts", "geojson", "laps")


@pytest.mark.parametrize("leaf", READ_PATHS)
def test_anon_cannot_read_activity_data(client, make_user, add_activity, leaf):
    aid = add_activity(user_id=make_user(), name="Private", points=TRACK)
    r = client.get(f"/api/activities/{aid}/{leaf}")
    assert r.status_code == 401, f"{leaf}: {r.status_code}"


@pytest.mark.parametrize("leaf", READ_PATHS)
def test_peer_cannot_read_unshared_activity(authed_client, make_user,
                                            add_activity, test_db, leaf):
    owner = make_user()
    _set_sharing(test_db, owner, activities=0)
    aid = add_activity(user_id=owner, name="Private", points=TRACK)

    r = authed_client.get(f"/api/activities/{aid}/{leaf}")
    assert r.status_code == 403, f"{leaf}: {r.status_code}"


@pytest.mark.parametrize("leaf", READ_PATHS)
def test_owner_can_read_own_activity(authed_client, add_activity, leaf):
    aid = add_activity(user_id=authed_client.user_id, name="Mine", points=TRACK)
    assert authed_client.get(f"/api/activities/{aid}/{leaf}").status_code == 200


@pytest.mark.parametrize("leaf", READ_PATHS)
def test_peer_can_read_shared_activity(authed_client, make_user, add_activity,
                                       test_db, leaf):
    """share_activities=1 is an opt-in to cross-user reads — compare.js relies on it."""
    owner = make_user()
    _set_sharing(test_db, owner, activities=1)
    aid = add_activity(user_id=owner, name="Shared", points=TRACK)

    r = authed_client.get(f"/api/activities/{aid}/{leaf}")
    assert r.status_code == 200, f"{leaf}: {r.text}"


def test_unowned_activity_stays_readable(authed_client, add_activity):
    """Legacy rows with user_id NULL remain visible to any logged-in user."""
    aid = add_activity(user_id=None, name="Legacy", points=TRACK)
    assert authed_client.get(f"/api/activities/{aid}/geojson").status_code == 200


def test_missing_activity_is_404_for_logged_in_user(authed_client):
    assert authed_client.get("/api/activities/999999/geojson").status_code == 404


# ── /api/schema ───────────────────────────────────────────────────────────────

def test_anon_cannot_read_schema(client):
    assert client.get("/api/schema").status_code == 401


def test_logged_in_user_can_read_schema(authed_client):
    assert authed_client.get("/api/schema").status_code == 200


# ── weather-location IDOR ─────────────────────────────────────────────────────

def test_anon_cannot_read_weather_location(client, make_user, add_activity):
    aid = add_activity(user_id=make_user(), name="Private", points=TRACK)
    assert client.get(f"/api/activities/{aid}/weather-location").status_code == 401


def test_peer_cannot_read_unshared_weather_location(authed_client, make_user,
                                                    add_activity, test_db):
    owner = make_user()
    _set_sharing(test_db, owner, activities=0)
    aid = add_activity(user_id=owner, name="Private", points=TRACK)
    r = authed_client.get(f"/api/activities/{aid}/weather-location")
    assert r.status_code == 403


# ── admin-only database endpoints ─────────────────────────────────────────────

def test_anon_cannot_create_database(client, tmp_path):
    target = tmp_path / "evil.ascentdb"
    r = client.post("/api/db/create", json={"path": str(target)})
    assert r.status_code == 401, r.text
    assert not target.exists(), "anon created a DB and repointed the app at it"


def test_anon_cannot_switch_database(client, db_path):
    r = client.post("/api/db/switch", json={"path": db_path})
    assert r.status_code == 401, r.text


def test_non_admin_cannot_create_database(authed_client, tmp_path):
    target = tmp_path / "evil.ascentdb"
    r = authed_client.post("/api/db/create", json={"path": str(target)})
    assert r.status_code == 403, r.text
    assert not target.exists()


def test_non_admin_cannot_switch_database(authed_client, db_path):
    r = authed_client.post("/api/db/switch", json={"path": db_path})
    assert r.status_code == 403, r.text


def test_admin_passes_the_db_switch_gate(client, make_user, db_path, monkeypatch):
    """The admin page's own call path must keep working.

    The switch itself is stubbed: performing it would repoint the process-wide
    DB and rewrite ASCENT_DB_PATH in the real .env. We assert the authorization
    decision, which is what this change is about.
    """
    from app.routers import settings as settings_mod
    calls = []
    monkeypatch.setattr(settings_mod, "_switch_to", lambda path: calls.append(path))

    from app.auth import create_session_token, SESSION_COOKIE
    client.cookies.set(SESSION_COOKIE,
                       create_session_token(make_user(is_admin=True)))
    r = client.post("/api/db/switch", json={"path": db_path})

    assert r.status_code == 200, r.text
    assert calls == [db_path], "admin was gated out of its own endpoint"


# ── segments: write + read ────────────────────────────────────────────────────

def _make_segment(test_db, activity_id, name="Seg"):
    import json
    test_db._ensure_segments_table()
    cur = test_db._con.execute(
        "INSERT INTO segments (name, activity_id, start_idx, end_idx, length_km, "
        "min_lat, max_lat, min_lon, max_lon, points_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,strftime('%s','now'))",
        (name, activity_id, 0, 2, 0.3, 40.0, 40.002, -105.002, -105.0,
         json.dumps([[p[0], p[1]] for p in TRACK])),
    )
    test_db._con.commit()
    return cur.lastrowid


def test_anon_cannot_create_segment(client, make_user, add_activity):
    aid = add_activity(user_id=make_user(), points=TRACK)
    r = client.post("/api/segments", json={
        "name": "Hack", "activity_id": aid, "start_idx": 0, "end_idx": 2})
    assert r.status_code == 401, r.text


def test_anon_cannot_delete_segment(client, make_user, add_activity, test_db):
    aid = add_activity(user_id=make_user(), points=TRACK)
    sid = _make_segment(test_db, aid)

    assert client.delete(f"/api/segments/{sid}").status_code == 401
    assert test_db.get_segment(sid) is not None


def test_peer_cannot_delete_another_users_segment(authed_client, make_user,
                                                  add_activity, test_db):
    aid = add_activity(user_id=make_user(), points=TRACK)
    sid = _make_segment(test_db, aid)

    assert authed_client.delete(f"/api/segments/{sid}").status_code == 403
    assert test_db.get_segment(sid) is not None


def test_peer_cannot_rename_another_users_segment(authed_client, make_user,
                                                  add_activity, test_db):
    aid = add_activity(user_id=make_user(), points=TRACK)
    sid = _make_segment(test_db, aid, name="Original")

    r = authed_client.patch(f"/api/segments/{sid}", json={"name": "Renamed"})
    assert r.status_code == 403, r.text
    assert test_db.get_segment(sid)["name"] == "Original"


def test_peer_cannot_rename_shared_segment(authed_client, make_user,
                                           add_activity, test_db):
    """share_segments grants READ, not write."""
    owner = make_user()
    _set_sharing(test_db, owner, segments=1)
    aid = add_activity(user_id=owner, points=TRACK)
    sid = _make_segment(test_db, aid, name="Original")

    r = authed_client.patch(f"/api/segments/{sid}", json={"name": "Renamed"})
    assert r.status_code == 403, r.text
    assert test_db.get_segment(sid)["name"] == "Original"


def test_owner_can_delete_own_segment(authed_client, add_activity, test_db):
    aid = add_activity(user_id=authed_client.user_id, points=TRACK)
    sid = _make_segment(test_db, aid)

    assert authed_client.delete(f"/api/segments/{sid}").status_code == 200
    assert test_db.get_segment(sid) is None


def test_anon_cannot_run_saved_segment_compare(client, make_user,
                                               add_activity, test_db):
    aid = add_activity(user_id=make_user(), points=TRACK)
    sid = _make_segment(test_db, aid)
    r = client.post("/api/segment/compare-saved",
                    json={"segment_id": sid, "activity_id": aid})
    assert r.status_code == 401, r.text


def test_anon_cannot_run_manual_segment_compare(client, make_user,
                                                add_activity, test_db):
    aid = add_activity(user_id=make_user(), points=TRACK)
    sid = _make_segment(test_db, aid)
    r = client.post("/api/segment/compare-manual",
                    json={"activity_ids": [aid], "segment_id": sid})
    assert r.status_code == 401, r.text


def test_peer_cannot_manual_compare_unshared_activity(authed_client, make_user,
                                                      add_activity, test_db):
    """compare-manual returns the named activities' tracks — each must be visible."""
    owner = make_user()
    _set_sharing(test_db, owner, activities=0, segments=1)
    aid = add_activity(user_id=owner, points=TRACK)
    sid = _make_segment(test_db, aid)
    r = authed_client.post("/api/segment/compare-manual",
                           json={"activity_ids": [aid], "segment_id": sid})
    assert r.status_code == 403, r.text


# ── Strava webhook: forged deauthorization ────────────────────────────────────

def _strava_user_with_ride(test_db, make_user, add_activity):
    uid = make_user()
    test_db._con.execute(
        "UPDATE users SET strava_athlete_id=?, strava_tokens_json=? WHERE id=?",
        ("12345", '{"access_token":"tok","refresh_token":"ref",'
                  '"expires_at":4000000000}', uid))
    test_db._con.commit()
    aid = add_activity(user_id=uid, name="Strava ride")
    test_db._con.execute(
        "UPDATE activities SET strava_activity_id=? WHERE id=?", (999, aid))
    test_db._con.commit()
    return uid, aid


def _stub_refresh(monkeypatch, *, revoked: bool):
    """Stand in for the Strava token refresh the webhook uses as proof."""
    import httpx
    from app.routers import strava as strava_mod

    async def _fake(tokens, user_id=None):
        if revoked:
            raise httpx.HTTPStatusError(
                "invalid_grant",
                request=httpx.Request("POST", strava_mod.STRAVA_TOKEN_URL),
                response=httpx.Response(400))
        return dict(tokens)

    monkeypatch.setattr(strava_mod, "refresh_tokens", _fake)


def test_forged_deauth_webhook_does_not_delete_activities(client, make_user,
                                                          add_activity, test_db,
                                                          monkeypatch):
    """An unauthenticated POST claiming an athlete deauthorized must not wipe
    their data while that athlete's stored Strava token is still valid."""
    uid, aid = _strava_user_with_ride(test_db, make_user, add_activity)
    _stub_refresh(monkeypatch, revoked=False)   # grant still alive → forged

    r = client.post("/strava/webhook", json={
        "object_type": "athlete", "aspect_type": "delete", "owner_id": 12345})

    assert r.status_code == 200, "Strava requires a 200 regardless"
    assert test_db.get_activity(aid) is not None, "forged webhook wiped data"
    row = test_db._con.execute(
        "SELECT strava_athlete_id FROM users WHERE id=?", (uid,)).fetchone()
    assert row[0] == "12345", "forged webhook cleared Strava credentials"


# ── Strava activity proxy ─────────────────────────────────────────────────────

def test_anon_cannot_proxy_strava_activities(client):
    """This endpoint spends the server's Strava quota — it must require a session."""
    assert client.get("/strava/api/activities").status_code == 401


def test_genuine_deauth_webhook_still_deletes_activities(client, make_user,
                                                         add_activity, test_db,
                                                         monkeypatch):
    """Strava's deauthorization contract must keep working: once the grant is
    really revoked, the athlete's imported data and credentials are removed."""
    uid, aid = _strava_user_with_ride(test_db, make_user, add_activity)
    _stub_refresh(monkeypatch, revoked=True)

    r = client.post("/strava/webhook", json={
        "object_type": "athlete", "aspect_type": "delete", "owner_id": 12345})

    assert r.status_code == 200
    assert test_db.get_activity(aid) is None, "genuine deauth did not delete"
    row = test_db._con.execute(
        "SELECT strava_athlete_id, strava_tokens_json FROM users WHERE id=?",
        (uid,)).fetchone()
    assert row[0] is None and row[1] is None, "credentials not cleared"


def test_deauth_webhook_fails_closed_on_network_error(client, make_user,
                                                      add_activity, test_db,
                                                      monkeypatch):
    """If we cannot reach Strava to confirm, keep the data."""
    import httpx
    from app.routers import strava as strava_mod
    uid, aid = _strava_user_with_ride(test_db, make_user, add_activity)

    async def _boom(tokens, user_id=None):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(strava_mod, "refresh_tokens", _boom)

    r = client.post("/strava/webhook", json={
        "object_type": "athlete", "aspect_type": "delete", "owner_id": 12345})

    assert r.status_code == 200
    assert test_db.get_activity(aid) is not None, "deleted data on an unconfirmed deauth"


def test_deauth_webhook_without_stored_tokens_does_not_delete(client, make_user,
                                                              add_activity, test_db):
    """No stored grant means nothing can prove the claim, so nothing is deleted.

    Guards the gap where a forged event could wipe any athlete whose tokens had
    already been cleared — the absence of a token is not evidence of revocation.
    """
    uid = make_user()
    test_db._con.execute(
        "UPDATE users SET strava_athlete_id=?, strava_tokens_json=NULL WHERE id=?",
        ("12345", uid))
    test_db._con.commit()
    aid = add_activity(user_id=uid, name="Strava ride")
    test_db._con.execute(
        "UPDATE activities SET strava_activity_id=? WHERE id=?", (999, aid))
    test_db._con.commit()

    r = client.post("/strava/webhook", json={
        "object_type": "athlete", "aspect_type": "delete", "owner_id": 12345})

    assert r.status_code == 200
    assert test_db.get_activity(aid) is not None, "forged webhook wiped data"


# ── secrets hygiene ───────────────────────────────────────────────────────────

def test_secret_key_refused_on_a_deployed_server(monkeypatch):
    """Signing sessions with the repo's published dev key must never happen on
    a server — the cookie would be forgeable by anyone who has read the source."""
    from app import auth as auth_mod
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("FLY_APP_NAME", "ascent-web")

    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        auth_mod._secret_key()


def test_secret_key_falls_back_only_on_a_dev_machine(monkeypatch):
    from app import auth as auth_mod
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("ASCENT_HTTPS", raising=False)

    assert auth_mod._secret_key() == auth_mod._DEV_SECRET_KEY


def test_configured_secret_key_is_used_everywhere(monkeypatch):
    from app import auth as auth_mod
    monkeypatch.setenv("SECRET_KEY", "a-real-key")
    monkeypatch.setenv("FLY_APP_NAME", "ascent-web")

    assert auth_mod._secret_key() == "a-real-key"


@pytest.mark.parametrize("env,value", [("FLY_APP_NAME", "ascent-web"),
                                       ("ASCENT_HTTPS", "true")])
def test_reset_link_is_not_logged_on_a_deployed_server(client, make_user, test_db,
                                                       monkeypatch, caplog, env, value):
    """The reset token is a credential; request logs are retained and readable."""
    import logging
    monkeypatch.setenv(env, value)
    monkeypatch.setenv("SECRET_KEY", "a-real-key")
    test_db._con.execute("UPDATE users SET email=? WHERE id=?",
                         ("reset@example.com", make_user()))
    test_db._con.commit()

    with caplog.at_level(logging.DEBUG):
        r = client.post("/forgot-password", data={"email": "reset@example.com"},
                        follow_redirects=False)

    assert r.status_code == 303
    assert "reset-password?token=" not in caplog.text, "reset token leaked to logs"


def test_reset_link_is_logged_on_a_dev_machine_without_smtp(client, make_user,
                                                            test_db, monkeypatch,
                                                            caplog):
    """Locally with no SMTP the console is the only delivery channel — keep it."""
    import logging
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("ASCENT_HTTPS", raising=False)
    monkeypatch.setattr("app.mailer.smtp_configured", lambda: False)
    test_db._con.execute("UPDATE users SET email=? WHERE id=?",
                         ("reset2@example.com", make_user()))
    test_db._con.commit()

    with caplog.at_level(logging.DEBUG):
        client.post("/forgot-password", data={"email": "reset2@example.com"},
                    follow_redirects=False)

    assert "reset-password?token=" in caplog.text


@pytest.mark.parametrize("env,value", [("FLY_APP_NAME", "ascent-web"),
                                       ("ASCENT_HTTPS", "true")])
def test_deployed_server_refuses_to_start_without_secret_key(monkeypatch, env, value):
    """A bad deploy must fail its health check, not boot and serve broken auth."""
    from starlette.testclient import TestClient
    import app.main as main_mod
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv(env, value)

    with pytest.raises(RuntimeError, match="refusing to start"):
        with TestClient(main_mod.app):
            pass


def test_dev_server_starts_without_secret_key(monkeypatch, test_db):
    from starlette.testclient import TestClient
    import app.main as main_mod
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("ASCENT_HTTPS", raising=False)
    main_mod.set_db(test_db)

    with TestClient(main_mod.app) as c:
        assert c.get("/favicon.ico").status_code == 204


def test_deployed_server_starts_with_secret_key(monkeypatch, test_db):
    from starlette.testclient import TestClient
    import app.main as main_mod
    monkeypatch.setenv("SECRET_KEY", "a-real-key")
    monkeypatch.setenv("FLY_APP_NAME", "ascent-web")
    main_mod.set_db(test_db)

    with TestClient(main_mod.app) as c:
        assert c.get("/favicon.ico").status_code == 204


# ── post-login redirect must stay on this site ────────────────────────────────

def _user_with_password(test_db, make_user, password="correct-horse"):
    from app.auth import hash_password
    uid = make_user(password_hash=hash_password(password))
    email = test_db.get_user(uid)["email"]
    return email, password


OFFSITE_NEXTS = [
    "https://evil.example.com/phish",
    "http://evil.example.com",
    "//evil.example.com",            # protocol-relative — browsers go off-site
    "/\\evil.example.com",           # backslash variant of the same trick
    "javascript:alert(1)",
    "data:text/html,<script>1</script>",
    "/next\r\nSet-Cookie: a=b",      # header injection attempt
]


@pytest.mark.parametrize("bad", OFFSITE_NEXTS)
def test_login_refuses_offsite_redirect(client, make_user, test_db, bad):
    """A successful login must never hand the browser to another origin."""
    email, password = _user_with_password(test_db, make_user)

    r = client.post("/login", data={"email": email, "password": password,
                                    "next": bad}, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == "/", f"open redirect via {bad!r}"


@pytest.mark.parametrize("good", ["/", "/activities", "/tours?year=2026",
                                  "/activities#anchor"])
def test_login_preserves_same_site_redirect(client, make_user, test_db, good):
    """Legitimate deep links still work — that's what `next` is for."""
    email, password = _user_with_password(test_db, make_user)

    r = client.post("/login", data={"email": email, "password": password,
                                    "next": good}, follow_redirects=False)

    assert r.status_code == 303
    assert r.headers["location"] == good


@pytest.mark.parametrize("bad", OFFSITE_NEXTS)
def test_login_page_does_not_echo_offsite_next(client, bad):
    """The hidden field feeds the POST, so it must be sanitised on the way in."""
    r = client.get("/login", params={"next": bad})
    assert r.status_code == 200
    assert "evil.example.com" not in r.text
    assert "javascript:" not in r.text


def test_failed_login_keeps_next_but_cannot_inject_params(client, make_user, test_db):
    """The retry URL is built from `next`; it must not smuggle extra params."""
    email, _ = _user_with_password(test_db, make_user)

    r = client.post("/login", data={"email": email, "password": "wrong",
                                    "next": "/activities&error=Totally+fine"},
                    follow_redirects=False)

    assert r.status_code == 303
    loc = r.headers["location"]
    assert "Totally+fine" not in loc, f"attacker controlled the error text: {loc}"


def test_failed_login_round_trips_a_legitimate_next(client, make_user, test_db):
    email, _ = _user_with_password(test_db, make_user)

    r = client.post("/login", data={"email": email, "password": "wrong",
                                    "next": "/activities"},
                    follow_redirects=False)

    assert r.status_code == 303
    assert "next=%2Factivities" in r.headers["location"] or \
           "next=/activities" in r.headers["location"]


# ── second wave: routes that read the session but never gated on it ───────────
# These called get_session_user_id() and then passed the result straight into a
# user-scoped query. With no session uid was None, which several DB helpers
# treat as "no filter" rather than "no access".

ANON_JSON_GETS = [
    "/api/stats/recent",
    "/api/stats/monthly",
    "/api/stats/weekly",
    "/api/stats/yearly",
    "/api/coach/state",
    "/api/coach/messages",
    "/api/coach/goals/archived",
    "/api/coach/usage",
    "/api/settings/keys",
    "/strava/status",
]


@pytest.mark.parametrize("path", ANON_JSON_GETS)
def test_anon_is_refused_on_session_scoped_reads(client, path):
    assert client.get(path).status_code == 401, path


def test_anon_cannot_read_another_users_activities_via_stats(client, make_user,
                                                             add_activity):
    """The sharpest case: an unscoped get_activities() returned every user's rows."""
    add_activity(user_id=make_user(), name="VICTIM-PRIVATE-RIDE")

    r = client.get("/api/stats/recent")

    assert r.status_code == 401
    assert "VICTIM-PRIVATE-RIDE" not in r.text


def test_anon_cannot_read_another_users_coach_goal(client, make_user, test_db):
    """_active_goal(user_id=None) matched the first goal row of any user."""
    victim = test_db.get_user(make_user())["id"]
    con = test_db._con
    con.execute("""CREATE TABLE IF NOT EXISTS coach_goals (
        id INTEGER PRIMARY KEY, user_id INTEGER, goal_text TEXT,
        created_at INTEGER, archived_at INTEGER)""")
    con.execute("INSERT INTO coach_goals (user_id, goal_text, created_at) "
                "VALUES (?, 'VICTIM-SECRET-GOAL', 1)", (victim,))
    con.commit()

    for path in ("/api/coach/state", "/api/coach/messages"):
        r = client.get(path)
        assert r.status_code == 401, path
        assert "VICTIM-SECRET-GOAL" not in r.text, path


def test_anon_cannot_overwrite_the_server_anthropic_key(client, monkeypatch, tmp_path):
    """The no-session branch wrote the key into the server's .env and environ."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-original")

    r = client.post("/api/settings/anthropic-key", json={"key": "sk-ant-attacker"})

    assert r.status_code == 401, r.text
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-original"


def test_anon_cannot_spend_the_anthropic_key_via_chat(client):
    r = client.post("/api/coach/chat", json={"message": "hello"})
    assert r.status_code == 401, r.text


def test_anon_cannot_set_a_coach_goal(client):
    r = client.post("/api/coach/goal", json={"goal_text": "Win the Tour"})
    assert r.status_code == 401, r.text


def test_anon_cannot_delete_the_strava_token_file(client, tmp_path, monkeypatch):
    """GET /strava/disconnect unlinked the legacy token file with no session."""
    from app.routers import strava as strava_mod
    token_file = tmp_path / "strava_tokens.json"
    token_file.write_text('{"refresh_token":"keep-me"}')
    monkeypatch.setattr(strava_mod, "_tokens_path", lambda: token_file)

    r = client.get("/strava/disconnect", follow_redirects=False)

    assert r.status_code == 401, r.text
    assert token_file.exists(), "anon deleted the server's Strava token file"


def test_anon_strava_connect_goes_to_login(client):
    """Page routes bounce to login rather than 401."""
    r = client.get("/strava/connect", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_anon_cannot_run_segment_compare(client, make_user, add_activity):
    """Without a session the candidate search covered every user's activities."""
    aid = add_activity(user_id=make_user(), points=TRACK)
    r = client.post("/api/segment/compare",
                    json={"activity_id": aid, "start_idx": 0, "end_idx": 2})
    assert r.status_code == 401, r.text


def test_anon_cannot_list_segments_for_an_activity(client, make_user, add_activity):
    aid = add_activity(user_id=make_user(), points=TRACK)
    assert client.get(f"/api/segments/for-activity/{aid}").status_code == 401


def test_owner_still_reads_their_own_stats_and_coach_state(authed_client, add_activity):
    """The gating must not break the signed-in dashboard."""
    add_activity(user_id=authed_client.user_id, name="Mine")
    for path in ("/api/stats/recent", "/api/stats/monthly", "/api/stats/yearly",
                 "/api/coach/state", "/api/settings/keys"):
        assert authed_client.get(path).status_code == 200, path
