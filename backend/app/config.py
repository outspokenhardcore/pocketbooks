"""Central configuration and paths.

All state lives in a single data directory so PocketBooks cannot collide with
the original audiobookServer:
  macOS:  ~/Library/Application Support/PocketBooks
  override with env PBS_DATA_DIR (used by tests).

This is the headless *server* edition: no pywebview, a PDF library folder
instead of file pickers, on-demand HLS streaming, and a small audio cache.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# huggingface_hub 1.x retired hf_transfer in favor of the Xet backend. Clear
# the deprecated flag and switch on Xet here at import time — config is imported
# before huggingface_hub anywhere, so this also silences the warning in the main
# process even if the parent shell still exports the old variable.
os.environ.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")

APP_NAME = "PocketBooks"

# Name of a pocket-tts model config (the English 2026-10 model by default).
POCKET_MODEL = os.environ.get("PBS_MODEL", "english")
SAMPLE_RATE = 24000

# --- Robustness knobs (overridable via settings table / env) -----------------
CHUNK_TIMEOUT_BASE_S = int(os.environ.get("PBS_CHUNK_TIMEOUT", "60"))
CHUNK_TIMEOUT_PER_CHAR_S = 0.2
MODEL_LOAD_TIMEOUT_S = int(os.environ.get("PBS_LOAD_TIMEOUT", "300"))
CHUNK_MAX_ATTEMPTS = 3
WORKER_RECYCLE_EVERY_CHUNKS = 200
# Audio sanity: seconds of audio per character of text must fall in this band
SANITY_MIN_S_PER_CHAR = 0.005
SANITY_MAX_S_PER_CHAR = 0.5
SANITY_MIN_DURATION_S = 0.15

# --- Chunking ----------------------------------------------------------------
CHUNK_TARGET_CHARS = 350
CHUNK_MAX_CHARS = 400
CHUNK_MIN_CHARS = 200

# --- MP3 ----------------------------------------------------------------------
MP3_QUALITY = "4"
GAP_SENTENCE_S = 0.12
GAP_PARAGRAPH_S = 0.45

# --- HLS streaming ------------------------------------------------------------
# Finished chunks are grouped into ~TARGET_SEGMENT_S fMP4/AAC segments and
# appended to a growing EVENT playlist so iOS can start playing immediately.
TARGET_SEGMENT_S = 10.0
HLS_AAC_BITRATE = "64k"

# --- Audio cache (LRU) --------------------------------------------------------
# Per (pdf_hash, voice_id, format) we keep the HLS segments + final M4B.
# When the total exceeds the budget, least-recently-played finished books are
# evicted. The currently generating/playing book is never evicted.
AUDIO_CACHE_MAX_GB = float(os.environ.get("PBS_AUDIO_CACHE_GB", "20"))

# --- Server / remote access ---------------------------------------------------
# Bind on all interfaces by default so Tailnet devices can reach it; the API is
# still protected by a bearer token (see token_path) and should only be exposed
# over Tailscale/WireGuard, never the open internet.
SERVER_HOST = os.environ.get("PBS_HOST", "0.0.0.0")
SERVER_PORT = int(os.environ.get("PBS_PORT", "8725"))


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


# --- Demo mode ----------------------------------------------------------------
# A public demo server hands out one shared token, so anything that costs money
# or CPU on someone else's behalf has to be off. Demo mode disables the
# "previously on…" recap, which calls a paid third-party API per request.
# Everything needed to browse and listen stays available.
DEMO_MODE = _flag("PBS_DEMO", False)
RECAP_ENABLED = _flag("PBS_RECAP", not DEMO_MODE)

# Nightly sleep window: outside this window the service keeps the Mac awake
# (caffeinate) so it stays reachable; inside it the queue pauses and the Mac is
# allowed to sleep. Times are local "HH:MM". Window may wrap past midnight.
SLEEP_WINDOW_START = os.environ.get("PBS_SLEEP_START", "23:55")
SLEEP_WINDOW_END = os.environ.get("PBS_SLEEP_END", "04:00")


def data_dir() -> Path:
    env = os.environ.get("PBS_DATA_DIR")
    if env:
        d = Path(env)
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        d = Path.home() / f".{APP_NAME.lower()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "state.sqlite3"


def voices_dir() -> Path:
    d = data_dir() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def jobs_dir() -> Path:
    d = data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def library_dir() -> Path:
    """Folder of source PDFs the iOS app browses. New books are dropped here."""
    env = os.environ.get("PBS_LIBRARY_DIR")
    d = Path(env) if env else (Path.home() / "Audiobooks" / "PDFs")
    d.mkdir(parents=True, exist_ok=True)
    return d


def preclean_dir() -> Path:
    """Content-addressed cache of prepare_job() output, keyed by pdf hash."""
    d = data_dir() / "preclean"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_cache_dir() -> Path:
    """HLS segments + final M4B per (pdf_hash, voice, format)."""
    d = data_dir() / "audio_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path() -> Path:
    return data_dir() / "api_token.txt"


def recaps_dir() -> Path:
    d = data_dir() / "recaps"
    d.mkdir(parents=True, exist_ok=True)
    return d


def minimax_key() -> Optional[str]:
    """MiniMax API key for recap summaries (in <data>/minimax_key.txt)."""
    p = data_dir() / "minimax_key.txt"
    if p.exists():
        tok = p.read_text(encoding="utf-8").strip()
        return tok or None
    return os.environ.get("MINIMAX_API_KEY")


# --- Recap ("previously on") --------------------------------------------------
MMX_BIN = os.environ.get("PBS_MMX", "/opt/homebrew/bin/mmx")
MINIMAX_TEXT_MODEL = os.environ.get("PBS_MINIMAX_MODEL", "MiniMax-M2.7")
RECAP_TARGET_WORDS = int(os.environ.get("PBS_RECAP_WORDS", "70"))  # <= ~30 s spoken


def get_or_create_token() -> str:
    """Bearer token every API client must present. Auto-generated on first run;
    print it once so it can be copied into the iOS app / a tailnet secret."""
    p = token_path()
    if p.exists():
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    import secrets
    tok = secrets.token_urlsafe(32)
    p.write_text(tok, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return tok


def hf_env() -> dict:
    env = dict(os.environ)
    # huggingface_hub 1.x dropped hf_transfer in favor of the Xet backend.
    # Enable high-performance Xet transfers (needs the hf_xet package) and
    # drop the deprecated HF_HUB_ENABLE_HF_TRANSFER so it stops warning.
    env.pop("HF_HUB_ENABLE_HF_TRANSFER", None)
    env["HF_XET_HIGH_PERFORMANCE"] = "1"
    env.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    return env


def _parse_hhmm(s: str) -> tuple[int, int]:
    h, _, m = s.partition(":")
    return int(h), int(m)


def in_sleep_window(now=None) -> bool:
    """True if the local time is inside the nightly sleep window. While idle in
    this window the service stops caffeinate and lets the Mac sleep; outside it
    keeps the Mac awake so it stays reachable. A processing job overrides this."""
    import datetime
    now = now or datetime.datetime.now()
    cur = now.hour * 60 + now.minute
    sh, sm = _parse_hhmm(SLEEP_WINDOW_START)
    eh, em = _parse_hhmm(SLEEP_WINDOW_END)
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # window wraps past midnight


def ffmpeg_path() -> str:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / "vendor" / "ffmpeg", here / "vendor" / "ffmpeg"):
        if cand.exists():
            return str(cand)
    return os.environ.get("PBS_FFMPEG", "ffmpeg")
