"""Compare feature — the /api/segment/compare matcher should find activities that
pass through the same segment and exclude ones that don't. Activities + GPS points
are inserted into a real temp DB; the endpoint runs its full spatial matching."""


def _line(base_lat, base_lon, n=10, dlat=0.001):
    """A short north-heading track: n points, ~111 m apart → ~1 km total."""
    return [(base_lat + dlat * i, base_lon) for i in range(n)]


def test_segment_compare_finds_matching_and_excludes_far(authed_client, add_activity):
    uid = authed_client.user_id
    ref_pts   = _line(40.0000, -105.0)
    match_pts = _line(40.0002, -105.0)                    # ~22 m parallel — same segment
    far_pts   = _line(41.0000, -106.0)                    # nowhere near

    ref_id   = add_activity(user_id=uid, name="Ref",   points=ref_pts)
    match_id = add_activity(user_id=uid, name="Match", points=match_pts)
    far_id   = add_activity(user_id=uid, name="Far",   points=far_pts)

    r = authed_client.post("/api/segment/compare", json={
        "activity_id": ref_id, "start_idx": 0, "end_idx": 9,
    })
    assert r.status_code == 200, r.text
    ids = [m["activity_id"] for m in r.json()["matches"]]
    assert ref_id in ids            # reference is always included
    assert match_id in ids          # the overlapping activity is found
    assert far_id not in ids        # the far-away one is excluded


def test_segment_compare_no_other_matches_returns_404(authed_client, add_activity):
    uid = authed_client.user_id
    ref_id = add_activity(user_id=uid, name="Ref", points=_line(40.0, -105.0))
    add_activity(user_id=uid, name="Far", points=_line(41.0, -106.0))

    r = authed_client.post("/api/segment/compare", json={
        "activity_id": ref_id, "start_idx": 0, "end_idx": 9,
    })
    # Only the reference passes through the segment → not enough to compare.
    assert r.status_code == 404


def test_segment_compare_explicit_candidates_are_trusted(authed_client, add_activity):
    # candidate_ids bypasses spatial filtering (lenient matching) — an explicitly
    # chosen activity is always compared.
    uid = authed_client.user_id
    ref_id  = add_activity(user_id=uid, name="Ref",  points=_line(40.0000, -105.0))
    cand_id = add_activity(user_id=uid, name="Cand", points=_line(40.0002, -105.0))

    r = authed_client.post("/api/segment/compare", json={
        "activity_id": ref_id, "start_idx": 0, "end_idx": 9,
        "candidate_ids": [cand_id],
    })
    assert r.status_code == 200, r.text
    ids = [m["activity_id"] for m in r.json()["matches"]]
    assert ref_id in ids and cand_id in ids


def test_segment_compare_missing_reference_points_404(authed_client, add_activity):
    uid = authed_client.user_id
    # Activity with no GPS points at all.
    empty_id = add_activity(user_id=uid, name="NoGPS", points=None)
    r = authed_client.post("/api/segment/compare", json={
        "activity_id": empty_id, "start_idx": 0, "end_idx": 9,
    })
    assert r.status_code == 404
