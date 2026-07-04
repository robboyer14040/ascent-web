"""AI Coach features. Two layers:
  1. the pure prompt/profile/analysis builders (no key, no network);
  2. the end-to-end coach flow (set goal → chat) with the Anthropic HTTP call
     mocked to a captured-shape response, proving the feature returns something
     and persists it — without spending tokens.
"""

import json
import sqlite3

from app.routers.coach import (
    _build_system_prompt, _model_info, _build_activity_summary,
    DEFAULT_MODEL, MODELS,
)
from app.coach_analysis import (
    compute_age, build_athlete_profile_block, build_training_analysis,
)
from tests.samples import ANTHROPIC_MESSAGE_RESPONSE

REPLY_TEXT = ANTHROPIC_MESSAGE_RESPONSE["content"][0]["text"]


# ── fake Anthropic transport ──────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, data, status=200):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    @property
    def text(self):
        return json.dumps(self._data)


class _FakeAsyncClient:
    """Drop-in for httpx.AsyncClient used as an async context manager."""
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        return _FakeResp(ANTHROPIC_MESSAGE_RESPONSE)


# ── pure builders (no network) ────────────────────────────────────────────────

def test_compute_age():
    assert compute_age({"age": 41}) == 41
    assert compute_age({"birthday": "1985-01-01"}) >= 40   # relative to today
    assert compute_age({}) is None
    assert compute_age({"age": 0, "birthday": None}) is None


def test_model_info_known_and_fallback():
    assert _model_info(DEFAULT_MODEL)["label"] == MODELS[DEFAULT_MODEL]["label"]
    assert _model_info("nonexistent-model") == MODELS[DEFAULT_MODEL]   # falls back


def test_athlete_profile_block_empty_cases(test_db, make_user):
    assert build_athlete_profile_block(test_db, None) == ""
    uid = make_user()
    # profile row absent / all-blank → empty block
    assert build_athlete_profile_block(test_db, uid) == ""


def test_athlete_profile_block_populated(test_db, make_user):
    uid = make_user()
    test_db.set_user_profile(uid, max_hr=185, ftp_watts=250, age=41, weight_lb=160)
    block = build_athlete_profile_block(test_db, uid)
    assert "ATHLETE PROFILE" in block
    assert "Age: 41" in block
    assert "Max HR: 185 bpm" in block
    assert "FTP: 250 W" in block
    assert "HR zones" in block and "Power zones" in block
    assert "NOTE:" in block                       # age-aware recovery note


def test_build_system_prompt_includes_context():
    p = _build_system_prompt("Sub-3 marathon", "SUMMARY_TEXT",
                             profile_block="PROFILE_BLOCK",
                             analysis_block="ANALYSIS_BLOCK",
                             target_date="2026-10-01")
    assert "Sub-3 marathon" in p
    assert "SUMMARY_TEXT" in p
    assert "PROFILE_BLOCK" in p and "ANALYSIS_BLOCK" in p
    assert "Target date: 2026-10-01" in p
    assert "endurance sports coach" in p


def test_builders_run_on_empty_db(test_db, make_user):
    uid = make_user()
    # These must not raise on a DB with no activities; they return strings.
    assert isinstance(_build_activity_summary(test_db, uid), str)
    assert isinstance(build_training_analysis(test_db, uid), str)


# ── end-to-end coach flow with mocked Anthropic ───────────────────────────────

def _n_assistant_msgs(db) -> int:
    con = sqlite3.connect(db.path)
    try:
        return con.execute(
            "SELECT COUNT(*) FROM coach_messages WHERE role='assistant'").fetchone()[0]
    finally:
        con.close()


def test_coach_goal_then_chat_returns_and_persists(authed_client, test_db, monkeypatch):
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    monkeypatch.setattr("app.routers.coach.httpx.AsyncClient", _FakeAsyncClient)

    # Set a goal → triggers an initial AI assessment.
    r = authed_client.post("/api/coach/goal", json={"goal_text": "Sub-3 marathon"})
    assert r.status_code == 200, r.text
    assert r.json()["initial_message"] == REPLY_TEXT
    assert _n_assistant_msgs(test_db) == 1        # persisted

    # Chat turn → another AI reply.
    r2 = authed_client.post("/api/coach/chat", json={"message": "How's my fitness?"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["reply"] == REPLY_TEXT
    assert _n_assistant_msgs(test_db) == 2


def test_coach_goal_without_api_key_errors(authed_client, monkeypatch):
    # No user key and no env key → the coach cannot call Claude.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("app.routers.coach.httpx.AsyncClient", _FakeAsyncClient)
    r = authed_client.post("/api/coach/goal", json={"goal_text": "Get faster"})
    assert r.status_code == 500
    assert "key" in r.text.lower()
