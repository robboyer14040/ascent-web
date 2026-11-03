"""Strava is rate-limited, so these tests count the calls the UI's flows make.

The kudos/comments badges are fetched for every activity the user selects, and
the popovers are opened on click. Each duplicated request here is a request
against the app's shared Strava quota.
"""

import json

import pytest


COMMENTS_PAYLOAD = [
    {"text": "Nice climb!", "athlete": {"firstname": "Ada", "lastname": "L"}},
    {"text": "Brutal day", "athlete": {"firstname": "Grace", "lastname": "H"}},
]


@pytest.fixture
def strava_calls(monkeypatch):
    """Stub httpx.AsyncClient.get and record every Strava URL requested."""
    calls = []

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
        def json(self):
            return self._payload
        @property
        def text(self):
            return json.dumps(self._payload)

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw):
            calls.append(url)
            if url.endswith("/comments"):
                return _Resp(COMMENTS_PAYLOAD)
            if url.endswith("/kudos"):
                return _Resp([{"firstname": "Ada", "lastname": "L"}])
            return _Resp({"kudos_count": 7, "comment_count": 2})

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


@pytest.fixture
def strava_activity(authed_client, add_activity, test_db):
    """An activity linked to Strava, owned by the logged-in user, with tokens."""
    uid = authed_client.user_id
    test_db._con.execute(
        "UPDATE users SET strava_tokens_json=? WHERE id=?",
        ('{"access_token":"tok","refresh_token":"ref","expires_at":4000000000}', uid))
    test_db._con.commit()
    aid = add_activity(user_id=uid, name="Ride")
    test_db._con.execute(
        "UPDATE activities SET strava_activity_id=? WHERE id=?", (999, aid))
    test_db._con.commit()
    return aid


def test_comments_endpoint_returns_count_and_the_comments(authed_client,
                                                          strava_activity,
                                                          strava_calls):
    """One request must carry everything the badge and the popover need."""
    r = authed_client.get(f"/api/activities/{strava_activity}/strava-comments")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["comment_count"] == 2
    assert [c["text"] for c in body["comments"]] == ["Nice climb!", "Brutal day"]
    assert body["comments"][0]["athlete_name"] == "Ada L"

    comment_calls = [u for u in strava_calls if u.endswith("/comments")]
    assert len(comment_calls) == 1, f"expected 1 Strava call, got {strava_calls}"


def test_badge_then_popover_costs_one_strava_call(authed_client, strava_activity,
                                                  strava_calls):
    """Previously the count and the list hit /comments separately — same payload,
    twice the quota. The popover should now need no further request."""
    authed_client.get(f"/api/activities/{strava_activity}/strava-comments")

    assert len([u for u in strava_calls if u.endswith("/comments")]) == 1


def test_comments_without_strava_link_makes_no_call(authed_client, add_activity,
                                                    strava_calls):
    aid = add_activity(user_id=authed_client.user_id, name="Manual ride")
    r = authed_client.get(f"/api/activities/{aid}/strava-comments")

    assert r.status_code == 200
    assert r.json() == {"comment_count": 0, "comments": []}
    assert strava_calls == [], "spent quota on an activity with no Strava id"


def test_comments_endpoint_still_requires_access(client, make_user,
                                                 add_activity, strava_calls):
    """The authorization added earlier must survive the refactor."""
    aid = add_activity(user_id=make_user(), name="Theirs")
    assert client.get(f"/api/activities/{aid}/strava-comments").status_code == 401
    assert strava_calls == []


def test_kudos_endpoints_still_work(authed_client, strava_activity, strava_calls):
    """Kudos keeps its two endpoints: the count is authoritative from the
    activity detail, the list comes from /kudos — different resources."""
    r1 = authed_client.get(f"/api/activities/{strava_activity}/strava-kudos")
    assert r1.json()["kudos_count"] == 7

    r2 = authed_client.get(f"/api/activities/{strava_activity}/strava-kudos-list")
    assert r2.json()["athletes"] == [{"firstname": "Ada", "lastname": "L"}]
