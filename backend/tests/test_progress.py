"""Cross-device listening position: last-write-wins merge."""


def test_progress_roundtrip(db):
    db.set_progress("book1", 12.5, 3600.0, "iphone", updated_at=1000)
    row = db.get_progress("book1")
    assert row["position_s"] == 12.5
    assert row["duration_s"] == 3600.0
    assert row["device_id"] == "iphone"


def test_progress_last_write_wins(db):
    db.set_progress("b", 100.0, None, "iphone", updated_at=2000)
    # An older update from another device must be ignored.
    merged = db.set_progress("b", 50.0, None, "ipad", updated_at=1000)
    assert merged["position_s"] == 100.0
    assert db.get_progress("b")["position_s"] == 100.0
    # A newer update wins.
    merged = db.set_progress("b", 200.0, None, "ipad", updated_at=3000)
    assert merged["position_s"] == 200.0
    assert merged["device_id"] == "ipad"


def test_progress_equal_timestamp_overwrites(db):
    db.set_progress("b", 10.0, None, "a", updated_at=5000)
    # Equal timestamp is treated as >= so the latest call wins (idempotent re-PUT).
    merged = db.set_progress("b", 11.0, None, "b", updated_at=5000)
    assert merged["position_s"] == 11.0


def test_progress_missing_returns_none(db):
    assert db.get_progress("nope") is None


def test_delete_progress(db):
    db.set_progress("b", 42.0, None, "x", updated_at=1)
    assert db.get_progress("b") is not None
    db.delete_progress("b")
    assert db.get_progress("b") is None
