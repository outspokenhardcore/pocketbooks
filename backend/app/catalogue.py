"""Export a finished library as a static catalogue.

A public demo needs no Python at all: once a book has been generated, every
byte the app needs is a file. This writes those files into a tree that any
plain web server can serve, plus a manifest describing it.

    python -m app.catalogue export --dest out/          # all ready books
    python -m app.catalogue export --dest out/ --voice 1
    python -m app.catalogue check --dest out/           # verify a built tree

Layout — flat on purpose, so nothing collides and every URL is relative:

    catalogue.json                    manifest (version, voice, books)
    covers/<book_id>.png
    books/<book_id>/chapters.json
    books/<book_id>/transcript.json
    books/<book_id>/book.m4b          when the download exists
    books/<book_id>/hls/index.m3u8    plus init.mp4 and seg_*.m4s

The JSON bodies are produced by the same helpers the live API uses, so the
client sees byte-identical shapes whether it talks to a server or to files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from . import config, library
from .db import DB

CATALOGUE_VERSION = 1
MANIFEST_NAME = "catalogue.json"


@dataclass
class CatalogueBook:
    book_id: str
    title: str
    author: str
    duration_s: Optional[float]
    chapters: int
    cover_url: Optional[str]
    chapters_url: str
    transcript_url: str
    hls_url: str
    m4b_url: Optional[str]
    bytes: int = 0


@dataclass
class Catalogue:
    version: int
    generated_at: float
    voice_id: int
    voice_name: str
    books: list[CatalogueBook] = field(default_factory=list)


def _copy_tree(src: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    for item in sorted(src.iterdir()):
        if item.is_dir():
            continue
        shutil.copy2(item, dest / item.name)
        total += item.stat().st_size
    return total


def _finished_playlist(path: Path) -> bool:
    """A growing EVENT playlist is still being written; a demo needs the
    finished VOD form, otherwise the player waits forever for more segments."""
    return "#EXT-X-ENDLIST" in path.read_text(encoding="utf-8", errors="replace")


def export(dest: str | Path, *, voice_id: Optional[int] = None,
           fmt: str = "m4b", with_download: bool = False,
           db: Optional[DB] = None) -> Catalogue:
    import time

    from .server import _chapters, _transcript  # same shapes as the live API

    owned = db is None
    db = db or DB()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    voices = db.voices()
    if voice_id is None:
        voice_id = voices[0]["id"] if voices else 1
    voice_name = next((v["name"] for v in voices if v["id"] == voice_id), str(voice_id))

    catalogue = Catalogue(version=CATALOGUE_VERSION, generated_at=time.time(),
                          voice_id=voice_id, voice_name=voice_name)
    skipped: list[str] = []

    for pdf in library._all_pdfs():
        book_id = library.file_hash(pdf)
        job = db.job_by_hash(book_id, voice_id, fmt)
        if not job or job.get("status") != "done":
            skipped.append(f"{pdf.name}: not generated for voice {voice_name}")
            continue

        # The job records where its HLS output went; fall back to the
        # conventional cache location for rows written before that column.
        stream_dir = job.get("stream_dir")
        hls = Path(stream_dir) if stream_dir else \
            config.audio_cache_dir() / f"{book_id}_{voice_id}_{fmt}" / "hls"
        if not (hls / "index.m3u8").exists():
            skipped.append(f"{pdf.name}: no HLS output in the cache")
            continue
        if not _finished_playlist(hls / "index.m3u8"):
            skipped.append(f"{pdf.name}: playlist has no ENDLIST (still generating)")
            continue

        book_dir = dest / "books" / book_id
        size = _copy_tree(hls, book_dir / "hls")

        chapters = _chapters(db, job["id"])
        (book_dir / "chapters.json").write_text(
            json.dumps({"chapters": chapters}, ensure_ascii=False), encoding="utf-8")
        (book_dir / "transcript.json").write_text(
            json.dumps({"lines": _transcript(db, job["id"])}, ensure_ascii=False),
            encoding="utf-8")

        # The finished M4B roughly doubles the catalogue size and the app's
        # catalogue mode has no offline download, so it is opt-in.
        m4b_url = None
        output = job.get("output_mp3_path") if with_download else None
        if output and Path(output).exists():
            suffix = Path(output).suffix or ".m4b"
            shutil.copy2(output, book_dir / f"book{suffix}")
            size += Path(output).stat().st_size
            m4b_url = f"books/{book_id}/book{suffix}"

        cover_url = None
        cover = library.ensure_cover(book_id)
        if cover and Path(cover).exists():
            (dest / "covers").mkdir(parents=True, exist_ok=True)
            shutil.copy2(cover, dest / "covers" / f"{book_id}.png")
            cover_url = f"covers/{book_id}.png"

        duration = None
        meta = db.query_one("SELECT SUM(duration) AS d FROM chunks "
                            "WHERE job_id=? AND status='done'", (job["id"],))
        if meta and meta["d"]:
            duration = float(meta["d"])

        catalogue.books.append(CatalogueBook(
            book_id=book_id,
            title=job.get("title") or pdf.stem,
            author=job.get("author") or "",
            duration_s=duration,
            chapters=len(chapters),
            cover_url=cover_url,
            chapters_url=f"books/{book_id}/chapters.json",
            transcript_url=f"books/{book_id}/transcript.json",
            hls_url=f"books/{book_id}/hls/index.m3u8",
            m4b_url=m4b_url,
            bytes=size,
        ))

    catalogue.books.sort(key=lambda b: b.title.lower())
    (dest / MANIFEST_NAME).write_text(
        json.dumps(asdict(catalogue), indent=2, ensure_ascii=False),
        encoding="utf-8")

    export.skipped = skipped  # type: ignore[attr-defined]
    if owned:
        db.close()
    return catalogue


def check(dest: str | Path) -> list[str]:
    """Every path the manifest promises must exist and be non-empty."""
    dest = Path(dest)
    manifest = dest / MANIFEST_NAME
    if not manifest.exists():
        return [f"{MANIFEST_NAME} is missing"]
    data = json.loads(manifest.read_text(encoding="utf-8"))
    problems: list[str] = []
    if data.get("version") != CATALOGUE_VERSION:
        problems.append(f"unknown catalogue version {data.get('version')}")
    if not data.get("books"):
        problems.append("the catalogue contains no books")

    for book in data.get("books", []):
        for key in ("cover_url", "chapters_url", "transcript_url",
                    "hls_url", "m4b_url"):
            rel = book.get(key)
            if not rel:
                continue
            path = dest / rel
            if not path.exists():
                problems.append(f"{book['title']}: missing {rel}")
            elif path.stat().st_size == 0:
                problems.append(f"{book['title']}: empty {rel}")
        playlist = dest / book.get("hls_url", "")
        if playlist.exists():
            text = playlist.read_text(encoding="utf-8", errors="replace")
            if "#EXT-X-ENDLIST" not in text:
                problems.append(f"{book['title']}: playlist is not finished")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    if line.startswith("#EXT-X-MAP:URI="):
                        line = line.split('"')[1]
                    else:
                        continue
                if not (playlist.parent / line).exists():
                    problems.append(f"{book['title']}: segment {line} is missing")
    return problems


def _human(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.catalogue",
        description="Export a generated library as a static catalogue.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("export", help="write the static tree")
    run.add_argument("--dest", required=True)
    run.add_argument("--voice", type=int, default=None,
                     help="voice id to export (default: the first one)")
    run.add_argument("--with-download", action="store_true",
                     help="also publish the finished M4B (about doubles the size)")

    verify = sub.add_parser("check", help="verify an existing tree")
    verify.add_argument("--dest", required=True)

    args = parser.parse_args(argv)

    if args.command == "check":
        problems = check(args.dest)
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        print("catalogue is complete" if not problems
              else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    catalogue = export(args.dest, voice_id=args.voice,
                       with_download=args.with_download)
    for note in getattr(export, "skipped", []):
        print(f"  = skipped {note}")
    total = sum(b.bytes for b in catalogue.books)
    for book in catalogue.books:
        print(f"  + {book.title} ({book.chapters} chapters, {_human(book.bytes)})")
    if not catalogue.books:
        print("Nothing to export: generate the books first, then re-run.",
              file=sys.stderr)
        return 1
    print(f"{len(catalogue.books)} book(s), {_human(total)} -> {args.dest}")

    problems = check(args.dest)
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
