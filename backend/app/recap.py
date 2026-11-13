"""“Previously on…” recaps.

On demand, summarize everything heard so far (up to the current position, so no
spoilers) into a short narration and speak it with the book's selected
voice. Summary text comes from MiniMax (via the `mmx` CLI); audio is produced by
the same TTS worker used for the book.

Runs asynchronously: POST starts a job, the client polls until the audio is
ready. Results are cached per (book, chapter) so re-opening a recap is instant.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from . import config
from .db import DB
from .pdfproc.chunking import split_into_chunks
from .supervisor import WorkerHandle

_RECAP_SYSTEM = (
    "You write a short spoken 'Previously on…' recap for someone resuming an "
    "audiobook. Use ONLY the provided text (everything they have heard so far) "
    "and stay strictly faithful to it: do NOT add, infer, embellish, or pad with "
    "anything beyond what is explicitly stated, and do NOT hint at what happens "
    "next. Keep it to at most ~30 seconds read aloud (about {words} words) — and "
    "shorter if little has happened; even 2–3 sentences is fine. Cover the key "
    "characters and what has happened so far, in flowing prose to be read aloud: "
    "no headings, no lists, no preamble like 'Here is'. Start directly."
)
_CONDENSE_SYSTEM = (
    "Condense this audiobook excerpt into 4–6 sentences capturing the characters "
    "and key events. Plain prose, no preamble."
)

_WINDOW_CHARS = 14000   # per map-reduce window when the heard text is long


def _minimax_chat(system: str, user_text: str, max_tokens: int = 700) -> str:
    """One MiniMax chat completion via the mmx CLI. Big text goes over stdin
    (a messages file) to avoid command-line length limits."""
    key = config.minimax_key()
    if not key:
        raise RuntimeError("no MiniMax API key (data/minimax_key.txt)")
    import os
    env = dict(os.environ)
    env["MINIMAX_API_KEY"] = key
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    msgs = json.dumps([{"role": "user", "content": user_text}])
    proc = subprocess.run(
        [config.MMX_BIN, "text", "chat", "--system", system,
         "--messages-file", "-", "--model", config.MINIMAX_TEXT_MODEL,
         "--max-tokens", str(max_tokens), "--quiet", "--non-interactive"],
        input=msgs, capture_output=True, text=True, env=env, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"mmx failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def gather_heard_text(db: DB, job_id: int, position_s: float) -> tuple[str, int]:
    """All chunk text from the start up to position_s. Returns (text, chapter)."""
    rows = db.query(
        "SELECT * FROM chunks WHERE job_id=? AND status='done' ORDER BY idx",
        (job_id,))
    parts: list[str] = []
    last_chapter = 0
    t = 0.0
    for i, ch in enumerate(rows):
        if t > position_s and parts:
            break
        parts.append(ch["text"] or "")
        last_chapter = int(ch["chapter"] or 0)
        t += float(ch["duration"] or 0)
        if i < len(rows) - 1:
            t += (config.GAP_PARAGRAPH_S if ch["para_end"] else config.GAP_SENTENCE_S)
    return " ".join(p for p in parts if p), last_chapter


def summarize(text: str) -> str:
    """Map-reduce summary -> a ~RECAP_TARGET_WORDS 'previously on' recap."""
    words = config.RECAP_TARGET_WORDS
    recap_sys = _RECAP_SYSTEM.format(words=words)
    # NOTE: MiniMax-M2 is a reasoning model — it spends tokens "thinking" before
    # the answer, so max_tokens must stay generous or the reply comes back empty.
    # Brevity is enforced by the prompt (~70 words), not by max_tokens.
    if len(text) <= _WINDOW_CHARS:
        return _minimax_chat(recap_sys, "Everything heard so far:\n\n" + text,
                             max_tokens=1200)
    # Long: condense windows, then recap the condensed notes.
    minis = []
    for start in range(0, len(text), _WINDOW_CHARS):
        window = text[start:start + _WINDOW_CHARS]
        minis.append(_minimax_chat(_CONDENSE_SYSTEM, window, max_tokens=1200))
    return _minimax_chat(recap_sys, "Story notes so far:\n\n" + "\n".join(minis),
                         max_tokens=1200)


def _synthesize(text: str, voice: dict, out_dir: Path, log_file) -> Optional[Path]:
    """Speak the recap in the book's cloned voice -> a single m4a file."""
    from .wavio import write_wav  # noqa: F401 (worker writes wavs itself)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks = [t for t, _ in split_into_chunks(text)]
    if not chunks:
        return None
    job_like = {"language": "auto"}
    worker = WorkerHandle(job_like, voice, out_dir, log_file)
    ev = worker.wait_event(config.MODEL_LOAD_TIMEOUT_S)
    if not ev or ev.get("event") != "ready":
        worker.kill()
        return None
    wavs: list[str] = []
    try:
        for i, chunk in enumerate(chunks):
            timeout = max(config.CHUNK_TIMEOUT_BASE_S,
                          len(chunk) * config.CHUNK_TIMEOUT_PER_CHAR_S)
            worker.send({"cmd": "chunk", "idx": i, "text": chunk})
            ev = worker.wait_event(timeout)
            if ev and ev.get("event") == "chunk_done":
                wavs.append(ev["wav"])
    finally:
        worker.quit_gracefully()
    if not wavs:
        return None
    concat = out_dir / "concat.txt"
    concat.write_text(
        "\n".join(f"file '{w}'" for w in wavs), encoding="utf-8")
    out = out_dir / "recap.m4a"
    cmd = [config.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(concat),
           "-ac", "1", "-ar", str(config.SAMPLE_RATE),
           "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if res.returncode != 0 or not out.exists():
        return None
    return out


class RecapManager:
    """Async recap generation with a per-(book, chapter) cache."""

    def __init__(self, db: DB):
        self.db = db
        self._lock = threading.Lock()
        self._recaps: dict[str, dict] = {}      # recap_id -> state dict
        self._by_cache: dict[str, str] = {}     # cache_key -> recap_id

    def request(self, job_id: int, position_s: float) -> str:
        job = self.db.job(job_id)
        if not job:
            raise KeyError("unknown job")
        # Cache by a 60 s position window — NOT by chapter. The recap covers
        # "everything up to here", so two positions in the same chapter differ;
        # a coarse key would hand back a recap that reaches far past where you are.
        bucket = int(position_s // 60)
        cache_key = f"{job.get('pdf_hash')}:p{bucket}"
        with self._lock:
            rid = self._by_cache.get(cache_key)
            if rid and self._recaps.get(rid, {}).get("state") in ("processing", "ready"):
                return rid
            rid = uuid.uuid4().hex
            self._recaps[rid] = {"state": "processing", "text": None,
                                 "audio": None, "error": None}
            self._by_cache[cache_key] = rid
        threading.Thread(target=self._generate,
                         args=(rid, job, position_s), daemon=True).start()
        return rid

    def get(self, recap_id: str) -> Optional[dict]:
        return self._recaps.get(recap_id)

    def _set(self, rid: str, **fields) -> None:
        with self._lock:
            self._recaps.setdefault(rid, {}).update(fields)

    def _generate(self, rid: str, job: dict, position_s: float) -> None:
        try:
            text, _ = gather_heard_text(self.db, job["id"], position_s)
            if len(text) < 200:
                self._set(rid, state="failed",
                          error="not enough listened yet for a recap")
                return
            recap_text = summarize(text).strip()
            if not recap_text:
                self._set(rid, state="failed", error="empty summary from model")
                return
            self._set(rid, text=recap_text)
            voice = self.db.voice(job["voice_id"]) if job["voice_id"] else None
            out_dir = config.recaps_dir() / rid
            log_path = config.logs_dir() / f"recap_{rid}.log"
            with open(log_path, "a", buffering=1) as log_file:
                audio = _synthesize(recap_text, voice, out_dir, log_file) \
                    if voice else None
            if audio:
                self._set(rid, state="ready", audio=str(audio))
            else:
                # Text is still useful even if narration failed.
                self._set(rid, state="ready", audio=None)
        except Exception as exc:  # noqa: BLE001
            self._set(rid, state="failed", error=str(exc))
