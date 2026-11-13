"""Audio-cache LRU eviction.

The iPhone keeps in-progress books locally (Audible-style), so the server cache
is just a "don't re-generate on re-listen" accelerator and can stay small. When
the total size of all ready, unpinned caches exceeds AUDIO_CACHE_MAX_GB, the
least-recently-played ones are deleted first. The currently generating/playing
book (keep_key) is never evicted.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config
from .db import DB


def dir_size(path: str | Path) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def enforce_budget(db: DB, keep_key: str | None = None) -> list[str]:
    """Evict least-recently-played ready caches until under budget.
    Returns the cache_keys that were evicted."""
    budget = int(config.AUDIO_CACHE_MAX_GB * (1 << 30))
    evicted: list[str] = []
    total = db.cache_total_bytes()
    if total <= budget:
        return evicted
    for row in db.evictable_caches():  # least-recently-accessed first
        if row["cache_key"] == keep_key:
            continue
        shutil.rmtree(row["dir"], ignore_errors=True)
        db.delete_cache(row["cache_key"])
        evicted.append(row["cache_key"])
        total -= int(row["size_bytes"] or 0)
        if total <= budget:
            break
    return evicted
