"""Audio-cache LRU eviction."""
from app import cache


def _seed(db, env, key, size, state="ready", last_access=None):
    d = env["data"] / "audio_cache" / key
    d.mkdir(parents=True, exist_ok=True)
    (d / "blob.bin").write_bytes(b"0" * size)
    db.upsert_cache(key, pdf_hash="h", voice_id=1, format="m4b", dir=str(d),
                    state=state, size_bytes=size)
    if last_access is not None:
        db.execute("UPDATE audio_cache SET last_access=? WHERE cache_key=?",
                   (last_access, key))
    return d


def test_dir_size(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 100)
    (tmp_path / "b").write_bytes(b"y" * 50)
    assert cache.dir_size(tmp_path) == 150
    assert cache.dir_size(tmp_path / "missing") == 0


def test_under_budget_keeps_everything(db, env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUDIO_CACHE_MAX_GB", 1.0)
    _seed(db, env, "k1", 1000)
    assert cache.enforce_budget(db) == []


def test_evicts_least_recently_played_first(db, env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUDIO_CACHE_MAX_GB", 0.0)  # force eviction
    old = _seed(db, env, "old", 1000, last_access=100)
    new = _seed(db, env, "new", 1000, last_access=900)
    evicted = cache.enforce_budget(db, keep_key=None)
    # Both are evictable but the least-recently-accessed goes first.
    assert evicted[0] == "old"
    assert not old.exists()


def test_keep_key_is_never_evicted(db, env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUDIO_CACHE_MAX_GB", 0.0)
    keep = _seed(db, env, "keep", 1000, last_access=1)
    _seed(db, env, "other", 1000, last_access=2)
    evicted = cache.enforce_budget(db, keep_key="keep")
    assert "keep" not in evicted
    assert keep.exists()
    assert db.get_cache("keep") is not None


def test_partial_caches_are_not_evicted(db, env, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "AUDIO_CACHE_MAX_GB", 0.0)
    _seed(db, env, "partial", 5000, state="partial", last_access=1)
    assert cache.enforce_budget(db) == []  # in-progress book is protected
