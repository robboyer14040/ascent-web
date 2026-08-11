"""AI Coach features. Three layers:
  1. the pure prompt/profile/analysis builders (no key, no network);
  2. the read-only database tools the coach calls, including their per-user
     ownership checks;
  3. the end-to-end coach flow (set goal → streamed chat) with the Anthropic SDK
     mocked, proving the agentic loop runs tools, streams, and persists —
     without spending tokens.
"""

import json
import sqlite3
import time
from types import SimpleNamespace

from app import coach_tools
from app.routers.coach import (
    _build_system_prompt, _build_date_anchor, _build_request_messages,
    _model_info, _build_activity_summary, _resolve_model,
    DEFAULT_MODEL, MODELS,
)
from app.coach_analysis import (
    compute_age, build_athlete_profile_block, build_training_analysis,
)
from tests.samples import ANTHROPIC_MESSAGE_RESPONSE

REPLY_TEXT = ANTHROPIC_MESSAGE_RESPONSE["content"][0]["text"]


# ── fake Anthropic SDK ────────────────────────────────────────────────────────
#
# The coach streams through anthropic.AsyncAnthropic().messages.stream(...).
# These fakes reproduce just enough of that surface: async-iterable text deltas
# plus a final message carrying stop_reason / content / usage.

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(tool_id, name, args):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=args)


def _final(content, stop_reason="end_turn", in_tok=100, out_tok=20):
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=in_tok, output_tokens=out_tok,
            cache_read_input_tokens=0, cache_creation_input_tokens=0,
        ),
    )


class _FakeStream:
    def __init__(self, turn, recorder):
        self._turn = turn
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        for block in self._turn.content:
            if block.type == "text":
                yield SimpleNamespace(
                    type="content_block_delta",
                    delta=SimpleNamespace(type="text_delta", text=block.text),
                )

    async def get_final_message(self):
        return self._turn


class _FakeMessages:
    def __init__(self, turns, recorder):
        self._turns = list(turns)
        self._recorder = recorder

    def stream(self, **kwargs):
        self._recorder["calls"].append(kwargs)
        turn = self._turns.pop(0) if self._turns else _final([_text_block(REPLY_TEXT)])
        return _FakeStream(turn, self._recorder)


class _FakeAnthropic:
    def __init__(self, *a, **k):
        self.messages = _FakeMessages(self._turns, self._recorder)


def fake_sdk(monkeypatch, turns=None):
    """Patch anthropic.AsyncAnthropic; return a recorder of request kwargs."""
    recorder = {"calls": []}
    turns = turns if turns is not None else [_final([_text_block(REPLY_TEXT)])]

    cls = type("_Patched", (_FakeAnthropic,), {"_turns": turns, "_recorder": recorder})
    import anthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", cls)
    return recorder


def sse_events(body: str):
    """Parse an SSE response body into [(event_name, payload_dict), ...]."""
    out = []
    for frame in body.split("\n\n"):
        name, data = None, ""
        for line in frame.split("\n"):
            if line.startswith("event: "):
                name = line[7:].strip()
            elif line.startswith("data: "):
                data += line[6:]
        if name and data:
            out.append((name, json.loads(data)))
    return out


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


def test_model_registry_resolves_retired_ids():
    # Every dropdown value must be a real key, or the server silently downgrades.
    assert DEFAULT_MODEL in MODELS
    assert _resolve_model("claude-sonnet-4-6") in MODELS      # the phantom id
    assert _resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"
    assert _resolve_model(None) == DEFAULT_MODEL
    assert _resolve_model("total-nonsense") == DEFAULT_MODEL


# ── the two bugs that made the coach feel weak ────────────────────────────────

def _attrs(**kw):
    """attributes_json is a flat [k, v, k, v, ...] array, not an object."""
    out = []
    for k, v in kw.items():
        out += [k, v]
    return out


def test_activity_summary_reports_real_sport_type(test_db, make_user, add_activity):
    """Regression: json_extract() cannot read the flat attributes_json array, so
    every activity used to render as the generic 'Activity'."""
    uid = make_user()
    add_activity(
        user_id=uid, distance_mi=54.3, creation_time_s=int(time.time()) - 86400,
        attrs=_attrs(activity="GravelRide", name="Tahoe Rim", totalClimb=3860.0),
    )
    summary = _build_activity_summary(test_db, uid)
    assert "GravelRide" in summary
    assert "Tahoe Rim" in summary
    assert "3860ft climb" in summary
    assert ": Activity " not in summary        # the old generic fallback


def test_activity_summary_marks_relative_days(test_db, make_user, add_activity):
    """Absolute dates alone force date arithmetic; every line carries a day count."""
    uid = make_user()
    add_activity(user_id=uid, creation_time_s=int(time.time()) - 86400,
                 attrs=_attrs(activity="Ride"))
    summary = _build_activity_summary(test_db, uid)
    assert "yesterday" in summary


def test_training_analysis_reads_attribute_climb(test_db, make_user, add_activity):
    """coach_analysis.py had the same json_extract bug."""
    uid = make_user()
    now = int(time.time())
    for i in range(3):
        add_activity(user_id=uid, distance_mi=20.0, creation_time_s=now - (i + 1) * 86400,
                     attrs=_attrs(activity="Ride", totalClimb=1000.0))
    block = build_training_analysis(test_db, uid)
    assert "3,000 ft climb" in block     # 3 × 1000, read via parse_attrs


def _athlete_today(db, uid):
    """The date the anchor will use — the athlete's zone, UTC when unknown."""
    from datetime import datetime
    from app.routers.coach import _athlete_tz
    return datetime.now(_athlete_tz(db, uid)).date()


def test_date_anchor_counts_down_to_target(test_db, make_user):
    uid = make_user()
    from datetime import timedelta
    today  = _athlete_today(test_db, uid)
    target = (today + timedelta(days=30)).isoformat()

    anchor = _build_date_anchor(test_db, uid, target)
    assert "CURRENT DATE ANCHOR" in anchor
    assert "30 days from today" in anchor
    assert "ignore it" in anchor          # instruction to override stale history
    # Today's real date must be present, spelled out.
    assert today.strftime("%B") in anchor
    assert str(today.day) in anchor


def test_date_anchor_handles_passed_and_missing_targets(test_db, make_user):
    uid = make_user()
    from datetime import timedelta
    past = (_athlete_today(test_db, uid) - timedelta(days=5)).isoformat()
    assert "passed 5 days ago" in _build_date_anchor(test_db, uid, past)
    # No target date, and a malformed one, must not raise.
    assert "CURRENT DATE ANCHOR" in _build_date_anchor(test_db, uid, None)
    assert "CURRENT DATE ANCHOR" in _build_date_anchor(test_db, uid, "not-a-date")


def test_dates_use_the_athletes_timezone_not_the_servers(test_db, make_user, add_activity):
    """A ride logged at 5pm Pacific must not be reported as the next day.

    The offset comes from the athlete's most recent activity; without it the
    server's own clock would shift daily/weekly boundaries by a day.
    """
    from datetime import datetime, timedelta, timezone as tz
    from app.routers.coach import _athlete_tz

    uid = make_user()
    # 01:30 UTC a few days ago — the same instant is the *previous* day in UTC-8.
    moment = (datetime.now(tz.utc) - timedelta(days=3)).replace(
        hour=1, minute=30, second=0, microsecond=0)
    utc_day     = moment.date()
    pacific_day = moment.astimezone(tz(timedelta(hours=-8))).date()
    assert pacific_day != utc_day, "fixture must straddle a date boundary"

    aid = add_activity(user_id=uid, creation_time_s=int(moment.timestamp()),
                       attrs=_attrs(activity="Ride"))

    con = sqlite3.connect(test_db.path)
    try:
        con.execute("UPDATE activities SET seconds_from_gmt_at_sync=? WHERE id=?",
                    (-8 * 3600, aid))
        con.commit()
    finally:
        con.close()

    assert _athlete_tz(test_db, uid) == tz(timedelta(hours=-8))
    summary = _build_activity_summary(test_db, uid)
    assert pacific_day.isoformat() in summary
    assert utc_day.isoformat() not in summary

    # A nonsense offset must be ignored rather than producing an invalid tz.
    con = sqlite3.connect(test_db.path)
    try:
        con.execute("UPDATE activities SET seconds_from_gmt_at_sync=? WHERE id=?",
                    (999_999, aid))
        con.commit()
    finally:
        con.close()
    assert _athlete_tz(test_db, uid) == tz.utc


def test_system_prompt_has_no_date(test_db):
    """The date lives only in the anchor — a date here would break prompt caching
    and give the model two competing sources of truth."""
    from datetime import date
    p = _build_system_prompt("Goal", "SUMMARY")
    assert str(date.today().year) not in p
    assert "Today's date" not in p


# ── export: copy / PDF ────────────────────────────────────────────────────────

def _seed_conversation(test_db, uid, turns):
    """Insert (role, content) rows for one goal; returns (goal_id, [msg_ids])."""
    from app.routers.coach import _ensure_tables
    con = sqlite3.connect(test_db.path)
    try:
        _ensure_tables(con)
        goal_id = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at, user_id, target_date) "
            "VALUES (?,?,?,?)",
            ("Ride 200 miles", int(time.time()), uid, "2099-06-01")).lastrowid
        ids, now = [], int(time.time())
        for i, (role, content) in enumerate(turns):
            ids.append(con.execute(
                "INSERT INTO coach_messages (goal_id, role, content, created_at, model) "
                "VALUES (?,?,?,?,?)",
                (goal_id, role, content, now + i,
                 "claude-opus-5" if role == "assistant" else None)).lastrowid)
        con.commit()
    finally:
        con.close()
    return goal_id, ids


def test_exchanges_pairs_prompts_with_replies():
    from app.routers.coach import _exchanges
    msgs = [
        {"role": "user", "content": "a", "id": 1},
        {"role": "assistant", "content": "A", "id": 2},
        {"role": "user", "content": "b", "id": 3},
        {"role": "user", "content": "c", "id": 4},      # follow-up before a reply
        {"role": "assistant", "content": "BC", "id": 5},
        {"role": "user", "content": "d", "id": 6},      # still unanswered
    ]
    ex = _exchanges(msgs)
    assert [len(e["prompt"]) for e in ex] == [1, 2, 1]
    assert [e["reply"]["id"] if e["reply"] else None for e in ex] == [2, 5, None]


def test_markdown_to_html_renders_and_escapes():
    from app.routers.coach import _markdown_to_html
    html = _markdown_to_html("## Week 1\n\n| Day | Session |\n|---|---|\n| Mon | Rest |")
    assert "<h2>Week 1</h2>" in html
    assert "<table>" in html and "<td>Rest</td>" in html
    # Model output must never become live markup in the PDF.
    assert "<script>" not in _markdown_to_html("<script>alert(1)</script>")


def test_single_exchange_pdf(authed_client, test_db):
    uid = authed_client.user_id
    _, ids = _seed_conversation(test_db, uid, [
        ("user", "How should I train?"),
        ("assistant", "## Plan\n\n- Ride **Z2** on Tuesday\n"),
        ("user", "And Wednesday?"),
        ("assistant", "Rest Wednesday."),
    ])
    r = authed_client.get(f"/api/coach/messages/{ids[1]}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"
    assert "attachment" in r.headers["content-disposition"]

    # Asking for a PDF of a *user* message is a bad request, not a crash.
    assert authed_client.get(f"/api/coach/messages/{ids[0]}/pdf").status_code == 400
    assert authed_client.get("/api/coach/messages/999999/pdf").status_code == 404


def test_full_conversation_pdf(authed_client, test_db):
    uid = authed_client.user_id
    goal_id, _ = _seed_conversation(test_db, uid, [
        ("user", "Question one"),
        ("assistant", "Answer one"),
        ("user", "Question two"),
        ("assistant", "Answer two"),
    ])
    r = authed_client.get(f"/api/coach/goals/{goal_id}/pdf")
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    assert len(r.content) > 1000


def test_pdf_export_refuses_another_users_conversation(authed_client, test_db, make_user):
    stranger = make_user(email="stranger@example.com")
    goal_id, ids = _seed_conversation(test_db, stranger, [
        ("user", "Private question"),
        ("assistant", "Private answer"),
    ])
    assert authed_client.get(f"/api/coach/goals/{goal_id}/pdf").status_code == 403
    assert authed_client.get(f"/api/coach/messages/{ids[1]}/pdf").status_code == 403


def test_streamed_reply_reports_its_message_id(authed_client, test_db, monkeypatch):
    """The UI needs the new row's id to wire up Copy / PDF on a fresh reply."""
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    fake_sdk(monkeypatch)

    authed_client.post("/api/coach/goal", json={"goal_text": "Ride far"})
    r = authed_client.post("/api/coach/chat", json={"message": "Hi"})
    done = [p for k, p in sse_events(r.text) if k == "done"]
    assert done and isinstance(done[0]["message_id"], int)

    # That id must resolve to a downloadable PDF.
    pdf = authed_client.get(f"/api/coach/messages/{done[0]['message_id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_goal_initial_assessment_is_exportable(authed_client, test_db, monkeypatch):
    """The first reply after setting a goal must be exportable too."""
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    fake_sdk(monkeypatch)

    d = authed_client.post("/api/coach/goal", json={"goal_text": "Ride 200 miles"}).json()
    assert isinstance(d["message_id"], int)
    pdf = authed_client.get(f"/api/coach/messages/{d['message_id']}/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_messages_endpoint_exposes_ids(authed_client, test_db):
    uid = authed_client.user_id
    _seed_conversation(test_db, uid, [("user", "q"), ("assistant", "a")])
    msgs = authed_client.get("/api/coach/messages").json()["messages"]
    assert all(isinstance(m["id"], int) for m in msgs)
    assert [m["role"] for m in msgs] == ["user", "assistant"]   # stable ordering


# ── coach tools ───────────────────────────────────────────────────────────────

def test_tools_reject_another_users_activity(test_db, make_user, add_activity):
    owner    = make_user(email="owner@example.com")
    stranger = make_user(email="stranger@example.com")
    aid = add_activity(user_id=owner, attrs=_attrs(activity="Ride"))

    for tool in ("get_activity_detail", "get_activity_streams"):
        ok = coach_tools.run_tool(test_db, owner, tool, {"activity_id": aid})
        assert "not yours" not in json.dumps(ok)

        denied = coach_tools.run_tool(test_db, stranger, tool, {"activity_id": aid})
        assert "not yours" in denied["error"]


def test_list_activities_scopes_and_filters(test_db, make_user, add_activity):
    owner    = make_user(email="a@example.com")
    stranger = make_user(email="b@example.com")
    ts = 1_700_000_000
    add_activity(user_id=owner, creation_time_s=ts, attrs=_attrs(activity="Ride"))
    add_activity(user_id=owner, creation_time_s=ts, attrs=_attrs(activity="Hike"))
    add_activity(user_id=stranger, creation_time_s=ts, attrs=_attrs(activity="Ride"))

    args = {"start_date": "2023-11-01", "end_date": "2023-12-01"}
    assert coach_tools.run_tool(test_db, owner, "list_activities", args)["count"] == 2
    typed = dict(args, activity_type="Hike")
    got = coach_tools.run_tool(test_db, owner, "list_activities", typed)
    assert got["count"] == 1 and got["activities"][0]["type"] == "Hike"


def test_run_tool_returns_errors_as_data(test_db, make_user):
    """A bad tool call must cost one turn, not crash the request."""
    uid = make_user()
    assert "error" in coach_tools.run_tool(test_db, uid, "no_such_tool", {})
    assert "error" in coach_tools.run_tool(test_db, uid, "list_activities",
                                           {"start_date": "bad", "end_date": "bad"})
    assert "error" in coach_tools.run_tool(test_db, uid, "get_zone_distribution",
                                           {"period": "fortnight"})
    assert "error" in coach_tools.run_tool(test_db, uid, "list_activities", {"nope": 1})
    assert "error" in coach_tools.run_tool(test_db, None, "get_personal_records", {})


def test_every_tool_schema_is_wired():
    names = [s["name"] for s in coach_tools.TOOL_SCHEMAS]
    assert names == coach_tools.TOOL_NAMES
    for schema in coach_tools.TOOL_SCHEMAS:
        assert schema["description"].strip()
        assert schema["input_schema"]["type"] == "object"
        assert coach_tools.describe_call(schema["name"], {})


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
    fake_sdk(monkeypatch)

    # Set a goal → triggers an initial AI assessment.
    r = authed_client.post("/api/coach/goal", json={"goal_text": "Sub-3 marathon"})
    assert r.status_code == 200, r.text
    assert r.json()["initial_message"] == REPLY_TEXT
    assert _n_assistant_msgs(test_db) == 1        # persisted

    # Chat turn → a streamed reply.
    r2 = authed_client.post("/api/coach/chat", json={"message": "How's my fitness?"})
    assert r2.status_code == 200, r2.text
    events = sse_events(r2.text)
    text = "".join(p["delta"] for k, p in events if k == "text")
    assert text == REPLY_TEXT
    assert any(k == "done" for k, _ in events)
    assert _n_assistant_msgs(test_db) == 2


def test_coach_goal_without_api_key_errors(authed_client, monkeypatch):
    # No user key and no env key → the coach cannot call Claude.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fake_sdk(monkeypatch)
    r = authed_client.post("/api/coach/goal", json={"goal_text": "Get faster"})
    assert r.status_code == 500
    assert "key" in r.text.lower()


def test_chat_runs_tool_loop_and_streams_trace(authed_client, test_db, monkeypatch):
    """A tool_use turn must execute the tool, report it, then stream the answer."""
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")

    turns = [
        # Turn 1: coach decides to look at zone data before answering.
        _final(
            [_text_block("Let me check your zones. "),
             _tool_block("toolu_1", "get_zone_distribution", {"period": "month"})],
            stop_reason="tool_use",
        ),
        # Turn 2: the actual answer.
        _final([_text_block("You are spending too long in Z3.")]),
    ]
    recorder = fake_sdk(monkeypatch, turns)

    authed_client.post("/api/coach/goal", json={"goal_text": "Ride 200 miles"})
    r = authed_client.post("/api/coach/chat", json={"message": "How should I train?"})
    assert r.status_code == 200, r.text
    events = sse_events(r.text)

    tools = [p["label"] for k, p in events if k == "tool"]
    assert tools == ["Reading time-in-zone for the month"]

    text = "".join(p["delta"] for k, p in events if k == "text")
    assert text == "Let me check your zones. \n\nYou are spending too long in Z3."

    # The tool result must have been fed back as a tool_result user message.
    last_call = recorder["calls"][-1]
    roles = [m["role"] for m in last_call["messages"]]
    assert roles[-1] == "user"
    assert last_call["messages"][-1]["content"][0]["type"] == "tool_result"
    # Only the final text is persisted, and only once.
    assert _n_assistant_msgs(test_db) == 2   # goal assessment + this reply


def test_chat_request_shape(authed_client, test_db, monkeypatch):
    """Tools, prompt caching, and the date anchor must all be on the wire."""
    uid = authed_client.user_id
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    recorder = fake_sdk(monkeypatch)

    authed_client.post("/api/coach/goal",
                       json={"goal_text": "Ride 200 miles", "target_date": "2099-01-01"})
    authed_client.post("/api/coach/chat", json={"message": "Plan my week"})

    call = recorder["calls"][-1]
    assert [t["name"] for t in call["tools"]] == coach_tools.TOOL_NAMES
    # Exactly one cache breakpoint, on the static system block.
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Opus 5 gets adaptive thinking + high effort.
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "high"}
    # The date anchor is the final message, as an operator-authority system turn.
    last = call["messages"][-1]
    assert last["role"] == "system"
    assert "CURRENT DATE ANCHOR" in last["content"]


def test_partial_reply_is_saved_when_stream_aborts(test_db, make_user, monkeypatch):
    """A dropped connection must not leave the athlete's question unanswered."""
    import asyncio
    from app.routers.coach import _stream_coach_reply

    uid = make_user()
    test_db.update_user_settings(uid, anthropic_api_key="sk-test")

    con = sqlite3.connect(test_db.path)
    try:
        from app.routers.coach import _ensure_tables
        _ensure_tables(con)
        goal_id = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at, user_id) VALUES (?,?,?)",
            ("Ride far", int(time.time()), uid)).lastrowid
        con.commit()
    finally:
        con.close()

    fake_sdk(monkeypatch, [_final([_text_block("Partial thought")])])

    async def abort_after_first_chunk():
        gen = _stream_coach_reply(test_db, goal_id, "Ride far", [], user_id=uid)
        async for kind, _ in gen:
            if kind == "text":
                break          # simulate the client going away mid-stream
        await gen.aclose()

    asyncio.run(abort_after_first_chunk())

    con = sqlite3.connect(test_db.path)
    try:
        row = con.execute(
            "SELECT content FROM coach_messages WHERE role='assistant'").fetchone()
    finally:
        con.close()
    assert row is not None, "aborted stream lost the reply entirely"
    assert "Partial thought" in row[0]
    assert "interrupted" in row[0]


def test_completed_reply_is_not_marked_interrupted(test_db, make_user, monkeypatch):
    import asyncio
    from app.routers.coach import _stream_coach_reply, _ensure_tables

    uid = make_user()
    con = sqlite3.connect(test_db.path)
    try:
        _ensure_tables(con)
        goal_id = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at, user_id) VALUES (?,?,?)",
            ("Ride far", int(time.time()), uid)).lastrowid
        con.commit()
    finally:
        con.close()

    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    fake_sdk(monkeypatch, [_final([_text_block("Complete answer")])])

    async def run():
        return [k async for k, _ in _stream_coach_reply(
            test_db, goal_id, "Ride far", [], user_id=uid)]

    assert "done" in asyncio.run(run())

    con = sqlite3.connect(test_db.path)
    try:
        rows = con.execute(
            "SELECT content FROM coach_messages WHERE role='assistant'").fetchall()
    finally:
        con.close()
    assert len(rows) == 1                       # persisted exactly once
    assert rows[0][0] == "Complete answer"
    assert "interrupted" not in rows[0][0]


def test_text_across_tool_turns_is_separated(test_db, make_user, monkeypatch):
    """Pre-tool and post-tool text must not run together into one word."""
    import asyncio
    from app.routers.coach import _stream_coach_reply, _ensure_tables

    uid = make_user()
    con = sqlite3.connect(test_db.path)
    try:
        _ensure_tables(con)
        goal_id = con.execute(
            "INSERT INTO coach_goals (goal_text, created_at, user_id) VALUES (?,?,?)",
            ("Ride far", int(time.time()), uid)).lastrowid
        con.commit()
    finally:
        con.close()

    test_db.update_user_settings(uid, anthropic_api_key="sk-test")
    fake_sdk(monkeypatch, [
        _final([_text_block("Checking your zones."),
                _tool_block("t1", "get_personal_records", {})], stop_reason="tool_use"),
        _final([_text_block("Here is the plan.")]),
    ])

    async def run():
        return "".join(p for k, p in
                       [(k, p) async for k, p in _stream_coach_reply(
                           test_db, goal_id, "Ride far", [], user_id=uid)]
                       if k == "text")

    assert asyncio.run(run()) == "Checking your zones.\n\nHere is the plan."


def test_older_model_gets_anchor_folded_into_user_turn(test_db, make_user):
    """Models that reject role:'system' still receive the anchor."""
    uid = make_user()
    history = [{"role": "user", "content": "What should I do?"}]

    msgs = _build_request_messages(test_db, history, False, uid, None, "claude-haiku-4-5")
    assert msgs[-1]["role"] == "user"
    assert "CURRENT DATE ANCHOR" in msgs[-1]["content"]
    assert msgs[-1]["content"].endswith("What should I do?")
    # No thinking/effort params for older models.
    from app.routers.coach import _model_kwargs
    assert _model_kwargs("claude-haiku-4-5") == {}
