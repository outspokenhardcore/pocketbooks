"""Final audiobook assembly: chunk WAVs -> M4B (AAC, chapters, cover) or MP3.

Chapter timestamps are exact: we know every chunk's audio duration and the
inserted silence gaps. Chapter titles come from the PDF TOC (frozen at job
start in jobs.chapters_json); without a TOC, long books get time-based
"Part N" chapters so players can still navigate.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from . import config
from .pdfproc.metadata import write_id3
from .wavio import write_wav

FALLBACK_CHAPTER_S = 30 * 60   # no TOC: synthetic chapter every 30 min
FALLBACK_MIN_TOTAL_S = 45 * 60


def assemble(job: dict, done_chunks: list[dict], work_dir: Path) -> Path:
    if (job.get("output_format") or "mp3") == "m4b":
        return _assemble("m4b", job, done_chunks, work_dir)
    return _assemble("mp3", job, done_chunks, work_dir)


def _make_silence(path: Path, seconds: float, sr: int = config.SAMPLE_RATE) -> Path:
    if not path.exists():
        write_wav(path, np.zeros(int(seconds * sr), dtype=np.float32), sr)
    return path


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
    return name[:180] or "audiobook"


def _ffescape_meta(value: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", value)


def _build_concat_and_chapters(job, done_chunks, work_dir):
    """Returns (concat_list_path, chapters [(title, start_s, end_s)])."""
    gap_s = _make_silence(work_dir / "gap_sentence.wav", config.GAP_SENTENCE_S)
    gap_p = _make_silence(work_dir / "gap_paragraph.wav", config.GAP_PARAGRAPH_S)

    titles = []
    if job.get("chapters_json"):
        try:
            titles = json.loads(job["chapters_json"])
        except (json.JSONDecodeError, TypeError):
            titles = []

    def esc(p: str) -> str:
        return str(p).replace("'", r"'\''")

    lines = []
    spans: list[tuple[int, float, float]] = []  # (chapter_idx, start, end)
    t = 0.0
    for i, ch in enumerate(done_chunks):
        wav = ch["wav_path"]
        if not wav or not Path(wav).exists():
            continue
        start = t
        lines.append(f"file '{esc(wav)}'")
        t += float(ch.get("duration") or 0)
        if i < len(done_chunks) - 1:
            gap, gap_len = ((gap_p, config.GAP_PARAGRAPH_S) if ch.get("para_end")
                            else (gap_s, config.GAP_SENTENCE_S))
            lines.append(f"file '{esc(gap)}'")
            t += gap_len
        ci = int(ch.get("chapter") or 0)
        if spans and spans[-1][0] == ci:
            spans[-1] = (ci, spans[-1][1], t)
        else:
            spans.append((ci, start, t))
    if not lines:
        raise RuntimeError("no chunk wav files found on disk")

    chapters = [(titles[ci] if ci < len(titles) else f"Chapter {ci + 1}", s, e)
                for ci, s, e in spans]
    total = chapters[-1][2] if chapters else 0
    if len(chapters) <= 1 and total > FALLBACK_MIN_TOTAL_S:
        chapters = [(f"Part {n + 1}", n * FALLBACK_CHAPTER_S,
                     min((n + 1) * FALLBACK_CHAPTER_S, total))
                    for n in range(int(total // FALLBACK_CHAPTER_S) + 1)
                    if n * FALLBACK_CHAPTER_S < total]

    concat_list = work_dir / "concat.txt"
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    return concat_list, chapters


def _write_ffmetadata(path: Path, job: dict,
                      chapters: list[tuple[str, float, float]]) -> None:
    album = job.get("series") or job.get("title") or ""
    out = [";FFMETADATA1",
           f"title={_ffescape_meta(job.get('title') or '')}",
           f"artist={_ffescape_meta(job.get('author') or '')}",
           f"album={_ffescape_meta(album)}",
           "genre=Audiobook"]
    if job.get("track"):
        out.append(f"track={_ffescape_meta(str(job['track']))}")
    for title, start, end in chapters:
        out += ["[CHAPTER]", "TIMEBASE=1/1000",
                f"START={int(start * 1000)}", f"END={int(end * 1000)}",
                f"title={_ffescape_meta(title)}"]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _assemble(fmt: str, job: dict, done_chunks: list[dict],
              work_dir: Path) -> Path:
    out_dir = Path(job["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    track = f"{job['track']} - " if job.get("track") else ""
    base = _safe_filename(track + (job.get("title")
                                   or Path(job["pdf_path"]).stem))
    out_path = out_dir / f"{base}.{fmt}"
    concat_list, chapters = _build_concat_and_chapters(job, done_chunks, work_dir)

    tmp = work_dir / f"out.tmp.{fmt}"
    cmd = [config.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(concat_list)]
    if fmt == "m4b":
        ffmeta = work_dir / "ffmetadata.txt"
        _write_ffmetadata(ffmeta, job, chapters)
        cmd += ["-f", "ffmetadata", "-i", str(ffmeta),
                "-map_metadata", "1", "-map_chapters", "1",
                "-ac", "1", "-ar", str(config.SAMPLE_RATE),
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    else:
        cmd += ["-ac", "1", "-ar", str(config.SAMPLE_RATE),
                "-codec:a", "libmp3lame", "-q:a", config.MP3_QUALITY, str(tmp)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
    if res.returncode != 0 or not tmp.exists():
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[:500]}")

    if fmt == "m4b":
        _write_mp4_tags(tmp, job)
    else:
        write_id3(tmp, title=job.get("title") or Path(job["pdf_path"]).stem,
                  artist=job.get("author") or "",
                  album=job.get("series") or job.get("title"),
                  track=job.get("track"), cover_path=job.get("cover_path"))
    tmp.replace(out_path)
    return out_path


def _write_mp4_tags(path: Path, job: dict) -> None:
    try:
        from mutagen.mp4 import MP4, MP4Cover
    except ImportError:
        if os.environ.get("PBS_ALLOW_NO_ID3") == "1":  # test sandbox only
            return
        raise
    mp4 = MP4(str(path))
    mp4["\xa9nam"] = [job.get("title") or Path(job["pdf_path"]).stem]
    mp4["\xa9alb"] = [job.get("series") or job.get("title") or ""]
    if job.get("author"):
        mp4["\xa9ART"] = [job["author"]]
    mp4["\xa9gen"] = ["Audiobook"]
    if job.get("track"):
        try:
            mp4["trkn"] = [(int(job["track"]), 0)]
        except (TypeError, ValueError):
            pass
    cover = job.get("cover_path")
    if cover and Path(cover).exists():
        data = Path(cover).read_bytes()
        fmt = (MP4Cover.FORMAT_PNG if data[:8].startswith(b"\x89PNG")
               else MP4Cover.FORMAT_JPEG)
        mp4["covr"] = [MP4Cover(data, imageformat=fmt)]
    mp4.save()
