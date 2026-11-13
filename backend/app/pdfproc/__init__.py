"""PDF -> clean chunk plan + metadata + cover.

Heavy imports (fitz/PyMuPDF) stay inside prepare_job so that the pure-stdlib
parts (cleaning/chunking/metadata parsing) are importable everywhere,
including the test sandbox.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .chunking import split_into_chunks
from .cleaning import clean_text
from .metadata import parse_filename, resolve_metadata, write_id3

__all__ = ["prepare_job", "prepare_or_load", "clean_text", "split_into_chunks",
           "parse_filename", "resolve_metadata", "write_id3"]

# Bump when extraction/front-matter logic changes. Cached plans whose metadata
# carries an older version are rebuilt automatically on next access.
EXTRACTION_VERSION = 3

# How far into a chapter the printed heading may start. Part/book dividers
# ("BOOK ONE — THE COMING OF THE MARTIANS") can sit above the chapter heading
# on the same page, so the match is not anchored to the very first character.
_HEADING_SEARCH_CHARS = 150


def _heading_pattern(title: str) -> re.Pattern | None:
    """Match a printed heading whose spacing/punctuation differs from the TOC.

    A TOC entry reads 'Chapter I: The Eve of the War' while the page prints
    'Chapter I' and 'The Eve of the War' on two lines. Matching word by word
    with a tolerant separator catches both spellings.
    """
    words = re.findall(r"[^\W_]+", title, flags=re.UNICODE)
    if not words:
        return None
    return re.compile(r"[\s\W_]{0,4}".join(re.escape(w) for w in words)
                      + r"[\s.:,;\-\u2013\u2014]*", re.I)


def _announce_heading(title: str, text: str) -> str:
    """Detach a heading that got glued to sentence one and re-announce it as
    its own paragraph: 'PROLOGUE The feeling...' -> 'Prologue.\\n\\nThe feeling...'
    so TTS speaks the title, pauses, then starts the prose."""
    sep = "" if title.rstrip()[-1:] in ".!?" else "."
    body = text.lstrip()
    pattern = _heading_pattern(title)
    if pattern:
        match = pattern.search(text[:_HEADING_SEARCH_CHARS + 2 * len(title)])
        # Only drop the duplicate when nothing but other heading lines precede
        # it — a prefix containing sentence punctuation is already prose.
        prefix = text[:match.start()] if match else ""
        if match and match.start() <= _HEADING_SEARCH_CHARS \
                and not re.search(r"[.!?]", prefix):
            body = text[match.end():].lstrip()
    return f"{title}{sep}\n\n{body}" if body else f"{title}{sep}"


def prepare_job(pdf_path: str, work_dir: Path,
                skip_front_matter: bool = True) -> dict:
    """Extract, clean, chunk (chapter-aware) and gather metadata/cover.

    Returns chunks as (text, para_end, chapter_idx) plus chapter_titles —
    chapter boundaries come from the PDF's embedded TOC when present.
    """
    from .chapters import build_chapters, detect_story_start
    from .extract import (extract_cover, extract_pages_with_report, get_toc,
                          pdf_metadata)
    meta = resolve_metadata(pdf_path, pdf_metadata(pdf_path))
    pages, extraction_report = extract_pages_with_report(pdf_path)
    toc = get_toc(pdf_path)
    story = None
    if skip_front_matter and not toc:
        story = detect_story_start(pages)
        extraction_report.update(story)
    else:
        extraction_report.update({
            "story_start_page": 1,
            "confidence": 1.0 if toc else 0.0,
            "reason": "TOC-based front-matter handling" if toc
                      else "front-matter skipping disabled",
            "skipped_pages": [],
        })
    chapters = build_chapters(pages, toc,
                              fallback_title=meta["title"],
                              skip_front_matter=skip_front_matter,
                              story_start_page=(story or {}).get("story_start_page"))
    multi = len(chapters) > 1  # only announce headings for real, multi-chapter TOCs

    chunks: list[tuple[str, bool, int]] = []
    titles: list[str] = []
    debug_text: list[str] = []
    for text_raw in chapters:
        title, raw = text_raw
        text = clean_text(raw)
        if not text:
            continue
        if multi:
            text = _announce_heading(title, text)
        ci = len(titles)
        titles.append(title)
        debug_text.append(f"=== {title}\n{text}")
        for t, para in split_into_chunks(text):
            chunks.append((t, para, ci))
    if not chunks:
        raise RuntimeError("PDF contains no extractable text "
                           "(scanned/image-only PDFs are not supported)")
    cover = extract_cover(pdf_path, work_dir)
    (work_dir / "clean_text.txt").write_text("\n\n".join(debug_text),
                                             encoding="utf-8")
    (work_dir / "extraction_report.json").write_text(
        json.dumps(extraction_report, indent=2, ensure_ascii=False),
        encoding="utf-8")
    return {**meta, "cover_path": cover, "chunks": chunks,
            "chapter_titles": titles,
            "extraction_report": extraction_report}


def prepare_or_load(pdf_path: str, pdf_hash: str, *,
                    skip_front_matter: bool = True) -> dict:
    """Pre-clean cache: run prepare_job once per PDF (keyed by content hash) and
    reuse the result on later conversions and for library listing.

    The cleaning/chunking step is cheap next to TTS, but caching it gives the
    library instant metadata/cover and lets a conversion start without re-parsing.
    Returns the same dict shape as prepare_job (chunks as lists, not tuples).
    """
    from . import __name__ as _pkg  # noqa: F401 (keep heavy imports lazy)
    from .. import config

    cache = config.preclean_dir() / pdf_hash
    plan_file = cache / "chunks.json"
    meta_file = cache / "meta.json"
    if plan_file.exists() and meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        cache_valid = (
            meta.get("extraction_version") == EXTRACTION_VERSION
            and bool(meta.get("skip_front_matter", True)) == bool(skip_front_matter)
        )
        if cache_valid:
            chunks = json.loads(plan_file.read_text(encoding="utf-8"))
            cover = cache / "cover.jpg"
            return {**meta, "chunks": chunks,
                    "cover_path": str(cover) if cover.exists() else None}

    cache.mkdir(parents=True, exist_ok=True)
    result = prepare_job(pdf_path, cache, skip_front_matter=skip_front_matter)
    # Normalize cover into the cache dir under a stable name.
    cover_out = None
    if result.get("cover_path") and Path(result["cover_path"]).exists():
        cover_out = cache / "cover.jpg"
        if Path(result["cover_path"]) != cover_out:
            shutil.copyfile(result["cover_path"], cover_out)
    meta = {k: result[k] for k in
            ("title", "author", "track", "series", "chapter_titles",
             "extraction_report")}
    meta["extraction_version"] = EXTRACTION_VERSION
    meta["skip_front_matter"] = bool(skip_front_matter)
    plan_file.write_text(json.dumps(result["chunks"]), encoding="utf-8")
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    # Return the JSON-canonical (list) form so the cache-miss and cache-hit
    # paths are identical for callers.
    chunks = json.loads(plan_file.read_text(encoding="utf-8"))
    return {**meta, "chunks": chunks,
            "cover_path": str(cover_out) if cover_out else None}
