"""The public shared-tour page renders, and carries the shared stage table.

The stage table (columns, Visible Columns picker, dividers) lives in
stage_table.js / stage_table.css, shared with the Tours page. These pin the
markup contract that module documents, so the share page cannot drift out of it.
"""

from tests.test_tours_backend import _gpx_file


def _shared_tour_html(c):
    r = c.post("/tours",
               data={"title": "T", "start_date": "2026-07-01",
                     "end_date": "2026-07-26", "shared": "1"},
               files=[_gpx_file("1.gpx"), _gpx_file("2.gpx")])
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    aid = c.post(f"/tours/{tid}/attempts",
                 data={"start_date": "2026-07-01", "end_date": "2026-07-10"}).json()["id"]
    token = c.post(f"/tours/{tid}/publish", json={"attempt_id": aid}).json()["token"]
    page = c.get(f"/tours/share/{token}")
    assert page.status_code == 200, page.text
    return page.text


def test_share_page_carries_the_shared_stage_table(authed_client):
    html = _shared_tour_html(authed_client)
    for frag in ('id="stage-h-scroll"', 'id="stage-col-head"', 'id="stage-list-v-scroll"',
                 'id="stage-list"', 'id="stage-hscroll-thumb"', 'id="stage-col-picker"',
                 'id="stage-col-gear-btn"', 'id="sidebar-resize"',
                 'StageTable.togglePicker', 'StageTable.init',
                 '/static/list_cols.js', '/static/stage_table.js', '/static/stage_table.css'):
        assert frag in html, f"missing {frag}"


def test_share_page_keeps_no_copy_of_the_table_code(authed_client):
    """Everything the table does now comes from stage_table.js."""
    html = _shared_tour_html(authed_client)
    for gone in ('STAGE_ALL_COLS', 'buildStageColHead', 'stageColWidthOverrides',
                 'TourStageDetail.stageList(document.getElementById'):
        assert gone not in html, f"leftover {gone}"


def test_tours_page_keeps_no_copy_of_the_table_code(authed_client):
    html = authed_client.get("/tour").text
    for gone in ('STAGE_ALL_COLS', 'const isStageColFixed', 'stageColWidthOverrides',
                 'function buildStageColHead', 'function buildStageColPicker',
                 'STAGE_SORT_KEY'):
        assert gone not in html, f"leftover {gone}"
    for frag in ('/static/list_cols.js', '/static/stage_table.js',
                 '/static/stage_table.css', 'StageTable.init'):
        assert frag in html, f"missing {frag}"
