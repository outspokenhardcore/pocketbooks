"""TTS worker — runs as a separate CHILD PROCESS per job so a hung TTS call
can always be SIGKILLed by the supervisor without taking down the app.

Protocol (line-based JSON over pipes, protocol lines prefixed with "@@" so
random library prints on stdout can't corrupt it):
  stdin  <- {"cmd":"chunk","idx":int,"text":str}
            {"cmd":"quit"}
  stdout -> @@{"event":"ready"}
            @@{"event":"chunk_done","idx":int,"wav":str,"duration":float}
            @@{"event":"chunk_failed","idx":int,"error":str}
            @@{"event":"fatal","error":str}

The model is loaded ONCE at startup and reused for every chunk.
The worker never touches the SQLite DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import numpy as np


def emit(obj: dict) -> None:
    sys.stdout.write("@@" + json.dumps(obj) + "\n")
    sys.stdout.flush()


def sanity_check(audio: np.ndarray, sr: int, text: str) -> str | None:
    """Return an error string if the audio looks like garbage."""
    from . import config
    if audio.size == 0:
        return "empty audio"
    if not np.all(np.isfinite(audio)):
        return "non-finite samples (NaN/Inf)"
    dur = audio.size / float(sr)
    if dur < config.SANITY_MIN_DURATION_S:
        return f"too short: {dur:.2f}s"
    chars = max(len(text), 1)
    spc = dur / chars
    if spc > config.SANITY_MAX_S_PER_CHAR:
        return f"runaway output: {dur:.1f}s for {chars} chars"
    if spc < config.SANITY_MIN_S_PER_CHAR:
        return f"suspiciously short: {dur:.2f}s for {chars} chars"
    if float(np.max(np.abs(audio))) < 1e-4:
        return "silent audio"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="pocket")
    ap.add_argument("--ref-audio", required=True)
    ap.add_argument("--ref-text-file", required=True)
    ap.add_argument("--language", default="auto")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    from .engine import create_engine
    from .wavio import write_wav

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ref_text = Path(args.ref_text_file).read_text(encoding="utf-8")

    try:
        engine = create_engine(args.engine)
        engine.load()
    except Exception as exc:
        emit({"event": "fatal", "error": f"model load failed: {exc}"})
        return 2
    emit({"event": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("cmd") == "quit":
            break
        if msg.get("cmd") != "chunk":
            continue
        idx, text = int(msg["idx"]), str(msg["text"])
        try:
            audio, sr = engine.synthesize(
                text=text, ref_audio=args.ref_audio,
                ref_text=ref_text, language=args.language)
            err = sanity_check(audio, sr, text)
            if err:
                emit({"event": "chunk_failed", "idx": idx, "error": err})
                continue
            wav = out_dir / f"chunk_{idx:06d}.wav"
            tmp = out_dir / f".chunk_{idx:06d}.wav.tmp"
            write_wav(tmp, audio, sr)
            os.replace(tmp, wav)
            emit({"event": "chunk_done", "idx": idx, "wav": str(wav),
                  "duration": audio.size / float(sr)})
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit({"event": "chunk_failed", "idx": idx, "error": str(exc)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
