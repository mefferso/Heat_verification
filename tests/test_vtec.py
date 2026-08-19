from app.vtec import group_vtec_rows


def test_group_vtec_rows_keeps_zone_validity():
    rows = [
        {"vtec_year": 2026, "eventid": 7, "phenomena": "HT", "significance": "Y", "ugc": "LAZ058", "issue": "2026-08-10T16:00:00Z", "expire": "2026-08-11T00:00:00Z", "url": "https://example.test/event", "product_id": "a", "last_product_id": "b"},
        {"vtec_year": 2026, "eventid": 7, "phenomena": "HT", "significance": "Y", "ugc": "LAZ060", "issue": "2026-08-10T17:00:00Z", "expire": "2026-08-11T01:00:00Z", "url": "https://example.test/event", "product_id": "a", "last_product_id": "b"},
    ]
    events = group_vtec_rows(rows)
    assert len(events) == 1
    event = events[0]
    assert event["product"] == "Heat Advisory"
    assert event["threshold_f"] == 108.0
    assert len(event["zones"]) == 2
    assert event["start_utc"] == "2026-08-10T16:00:00+00:00"
    assert event["end_utc"] == "2026-08-11T01:00:00+00:00"
