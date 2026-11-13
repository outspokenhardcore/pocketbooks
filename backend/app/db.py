"""SQLite persistence (WAL). Single-process access from the app; the worker
child processes never touch the DB — they report over stdout IPC.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS voices (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    ref_wav_path TEXT NOT NULL,
    ref_text TEXT NOT NULL DEFAULT '',
    source_path TEXT,
    sample_rate INTEGER,
    duration REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    pdf_path TEXT NOT NULL,
    voice_id INTEGER,
    output_dir TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'queued',
    -- queued | processing | paused | done | failed | cancelled
    total_chunks INTEGER NOT NULL DEFAULT 0,
    done_chunks INTEGER NOT NULL DEFAULT 0,
    failed_chunks INTEGER NOT NULL DEFAULT 0,
    title TEXT, author TEXT, track TEXT, cover_path TEXT, series TEXT,
    output_format TEXT NOT NULL DEFAULT 'm4b',  -- m4b | mp3
    skip_front_matter INTEGER NOT NULL DEFAULT 1,
    chapters_json TEXT,
    pdf_hash TEXT,          -- stable content id of the source PDF (book_id)
    extraction_version INTEGER NOT NULL DEFAULT 1,
    stream_dir TEXT,        -- HLS output dir (segments + index.m3u8)
    work_dir TEXT,
    created_at REAL NOT NULL,
    started_at REAL, finished_at REAL,
    synth_seconds REAL NOT NULL DEFAULT 0,
    error TEXT,
    output_mp3_path TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    job_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    para_end INTEGER NOT NULL DEFAULT 0,
    chapter INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed
    wav_path TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    duration REAL,
    error TEXT,
    PRIMARY KEY (job_id, idx)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- Last-listened position per book, synced across devices (last-write-wins).
-- book_id is the stable content hash of the source PDF, so the position
-- survives re-conversion and is independent of any single job row.
CREATE TABLE IF NOT EXISTS progress (
    book_id TEXT PRIMARY KEY,
    position_s REAL NOT NULL DEFAULT 0,
    duration_s REAL,
    device_id TEXT,
    updated_at REAL NOT NULL
);
-- Registry of cached audio outputs for LRU eviction. One row per
-- (pdf_hash, voice_id, format); `dir` holds the HLS segments + final M4B.
CREATE TABLE IF NOT EXISTS audio_cache (
    cache_key TEXT PRIMARY KEY,   -- f"{pdf_hash}:{voice_id}:{format}"
    pdf_hash TEXT NOT NULL,
    voice_id INTEGER,
    format TEXT NOT NULL,
    job_id INTEGER,
    dir TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'partial',  -- partial | ready
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_s REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_access REAL NOT NULL
);
"""

# Fresh DB for the server edition: schema above is authoritative; ALTER TABLE
# migrations add columns to existing databases without data loss.
_MIGRATIONS: list[str] = [
    "ALTER TABLE progress ADD COLUMN title TEXT",
    "ALTER TABLE progress ADD COLUMN author TEXT",
    "ALTER TABLE jobs ADD COLUMN extraction_version INTEGER NOT NULL DEFAULT 1",
]

BUILTIN_VOICES = (
    (1, "Marius", "marius"),
    (2, "Javert", "javert"),
    (3, "Peter", "peter_yearsley"),
    (4, "Bill", "bill_boerst"),
    (5, "Caro", "caro_davy"),
    (6, "Alba", "alba"),
)


class DB:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or config.db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            for mig in _MIGRATIONS:
                try:
                    self._conn.execute(mig)
                except sqlite3.OperationalError:
                    pass  # column already exists
            self._conn.executemany(
                "INSERT OR IGNORE INTO voices"
                "(id,name,ref_wav_path,ref_text,source_path,sample_rate,duration,created_at) "
                "VALUES(?,?,?,'','builtin:pocket',?,NULL,0)",
                [(voice_id, name, key, config.SAMPLE_RATE)
                 for voice_id, name, key in BUILTIN_VOICES],
            )

    # --- generic ---------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- settings ---------------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # --- voices -----------------------------------------------------------
    def add_voice(self, name: str, ref_wav_path: str, ref_text: str,
                  source_path: str, sample_rate: int, duration: float) -> int:
        cur = self.execute(
            "INSERT INTO voices(name,ref_wav_path,ref_text,source_path,sample_rate,duration,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (name, ref_wav_path, ref_text, source_path, sample_rate, duration, time.time()),
        )
        return cur.lastrowid

    def voices(self) -> list[dict]:
        return self.query("SELECT * FROM voices ORDER BY id")

    def voice(self, voice_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM voices WHERE id=?", (voice_id,))

    def update_voice(self, voice_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE voices SET {cols} WHERE id=?", (*fields.values(), voice_id))

    def delete_voice(self, voice_id: int) -> None:
        self.execute("DELETE FROM voices WHERE id=?", (voice_id,))

    # --- jobs --------------------------------------------------------------
    def add_job(self, pdf_path: str, voice_id: int, output_dir: str,
                language: str = "auto", output_format: str = "m4b",
                skip_front_matter: bool = True) -> int:
        cur = self.execute(
            "INSERT INTO jobs(pdf_path,voice_id,output_dir,language,"
            "output_format,skip_front_matter,created_at) VALUES(?,?,?,?,?,?,?)",
            (pdf_path, voice_id, output_dir, language, output_format,
             1 if skip_front_matter else 0, time.time()),
        )
        return cur.lastrowid

    def jobs(self) -> list[dict]:
        return self.query("SELECT * FROM jobs ORDER BY id")

    def job(self, job_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def delete_job(self, job_id: int) -> None:
        self.execute("DELETE FROM chunks WHERE job_id=?", (job_id,))
        self.execute("DELETE FROM jobs WHERE id=?", (job_id,))

    def next_queued_job(self) -> Optional[dict]:
        return self.query_one(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1")

    # --- chunks -------------------------------------------------------------
    def write_chunk_plan(self, job_id: int, chunks: list[tuple]) -> None:
        """Freeze the chunk plan at job start.
        chunks = [(text, para_end[, chapter]), ...]"""
        rows = []
        for i, c in enumerate(chunks):
            text, para = c[0], int(c[1])
            chapter = int(c[2]) if len(c) > 2 else 0
            rows.append((job_id, i, text, para, chapter))
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE job_id=?", (job_id,))
            self._conn.executemany(
                "INSERT INTO chunks(job_id,idx,text,para_end,chapter)"
                " VALUES(?,?,?,?,?)", rows)

    def chunks(self, job_id: int) -> list[dict]:
        return self.query("SELECT * FROM chunks WHERE job_id=? ORDER BY idx", (job_id,))

    def pending_chunks(self, job_id: int) -> list[dict]:
        return self.query(
            "SELECT * FROM chunks WHERE job_id=? AND status='pending' ORDER BY idx",
            (job_id,))

    def update_chunk(self, job_id: int, idx: int, **fields: Any) -> None:
        cols = ",".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE chunks SET {cols} WHERE job_id=? AND idx=?",
                     (*fields.values(), job_id, idx))

    def recover_interrupted(self) -> None:
        """On app start: jobs left 'processing' by a crash go back to 'queued'
        so the queue resumes from the checkpoint."""
        self.execute("UPDATE jobs SET status='queued' WHERE status='processing'")

    def done_prefix_len(self, job_id: int) -> int:
        """Length of the contiguous run of finished chunks starting at idx 0.
        The HLS playlist may only grow up to this point (segments must be
        appended in order even though chunks finish out of order)."""
        rows = self.query(
            "SELECT idx, status FROM chunks WHERE job_id=? ORDER BY idx", (job_id,))
        n = 0
        for r in rows:
            if r["status"] == "done":
                n += 1
            else:
                break
        return n

    def job_by_hash(self, pdf_hash: str, voice_id: int,
                    output_format: str) -> Optional[dict]:
        return self.query_one(
            "SELECT * FROM jobs WHERE pdf_hash=? AND voice_id=? "
            "AND output_format=? ORDER BY id DESC LIMIT 1",
            (pdf_hash, voice_id, output_format))

    def latest_job_for_book(self, pdf_hash: str) -> Optional[dict]:
        """Most recent job for a book that has generated audio — used to build a
        recap regardless of which voice/format, and for offline-played books."""
        return self.query_one(
            "SELECT * FROM jobs WHERE pdf_hash=? AND done_chunks>0 "
            "ORDER BY id DESC LIMIT 1", (pdf_hash,))

    # --- progress (cross-device resume) -----------------------------------
    def get_progress(self, book_id: str) -> Optional[dict]:
        return self.query_one("SELECT * FROM progress WHERE book_id=?", (book_id,))

    def set_progress(self, book_id: str, position_s: float,
                     duration_s: Optional[float], device_id: Optional[str],
                     updated_at: float, title: Optional[str] = None,
                     author: Optional[str] = None) -> dict:
        """Last-write-wins: only accept the update if it is newer than what we
        have. title/author are never overwritten with NULL (COALESCE keeps the
        last good value). Returns the authoritative row after the merge."""
        cur = self.get_progress(book_id)
        if cur is None or updated_at >= (cur["updated_at"] or 0):
            self.execute(
                "INSERT INTO progress(book_id,position_s,duration_s,device_id,"
                "updated_at,title,author) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(book_id) DO UPDATE SET "
                "position_s=excluded.position_s, duration_s=excluded.duration_s,"
                "device_id=excluded.device_id, updated_at=excluded.updated_at,"
                "title=COALESCE(excluded.title,progress.title),"
                "author=COALESCE(excluded.author,progress.author)",
                (book_id, position_s, duration_s, device_id,
                 updated_at, title, author))
            return self.get_progress(book_id)
        return cur

    def delete_progress(self, book_id: str) -> None:
        self.execute("DELETE FROM progress WHERE book_id=?", (book_id,))

    # --- audio cache registry (LRU) ---------------------------------------
    def get_cache(self, cache_key: str) -> Optional[dict]:
        return self.query_one(
            "SELECT * FROM audio_cache WHERE cache_key=?", (cache_key,))

    def upsert_cache(self, cache_key: str, **fields: Any) -> None:
        if self.get_cache(cache_key) is None:
            now = time.time()
            self.execute(
                "INSERT INTO audio_cache(cache_key,pdf_hash,voice_id,format,dir,"
                "created_at,last_access) VALUES(?,?,?,?,?,?,?)",
                (cache_key, fields.get("pdf_hash"), fields.get("voice_id"),
                 fields.get("format"), fields.get("dir", ""), now, now))
        if fields:
            cols = ",".join(f"{k}=?" for k in fields)
            self.execute(f"UPDATE audio_cache SET {cols} WHERE cache_key=?",
                         (*fields.values(), cache_key))

    def touch_cache(self, cache_key: str) -> None:
        self.execute("UPDATE audio_cache SET last_access=? WHERE cache_key=?",
                     (time.time(), cache_key))

    def cache_total_bytes(self) -> int:
        row = self.query_one(
            "SELECT COALESCE(SUM(size_bytes),0) AS s FROM audio_cache")
        return int(row["s"] if row else 0)

    def evictable_caches(self) -> list[dict]:
        """Finished, unpinned caches, least-recently-played first."""
        return self.query(
            "SELECT * FROM audio_cache WHERE state='ready' AND pinned=0 "
            "ORDER BY last_access ASC")

    def delete_cache(self, cache_key: str) -> None:
        self.execute("DELETE FROM audio_cache WHERE cache_key=?", (cache_key,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
