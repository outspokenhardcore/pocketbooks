"""Title/author/track resolution + ID3 tagging with cover art."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


def parse_filename(stem: str) -> tuple[Optional[str], str]:
    """'12 - The Time Machine' -> ('12', 'The Time Machine')."""
    m = re.match(r"^(\d+)\s*[-–.]\s*(.+)$", stem)
    if m:
        return m.group(1), m.group(2).strip()
    return None, stem.strip()


def split_series(name_title: str) -> tuple[Optional[str], str]:
    """'Dark Nest_The Joiner King' -> ('Dark Nest', 'The Joiner King').

    The 'series_title' convention (underscore separator) lets audiobook
    players group a whole series under one album and order it by track.
    """
    if "_" in name_title:
        series, _, title = name_title.partition("_")
        series, title = series.strip(), title.strip()
        if series and title:
            return series, title
    return None, name_title


def resolve_metadata(pdf_path: str | Path, pdf_meta: dict) -> dict:
    stem = Path(pdf_path).stem
    track, name_title = parse_filename(stem)
    series, name_title = split_series(name_title)
    title = (pdf_meta.get("title") or "").strip() or name_title
    author = (pdf_meta.get("author") or "").strip()
    return {"title": title, "author": author, "track": track,
            "series": series}


def write_id3(mp3_path: str | Path, *, title: str, artist: str = "",
              album: Optional[str] = None, track: Optional[str] = None,
              genre: str = "Audiobook", cover_path: Optional[str] = None) -> None:
    try:
        from mutagen.id3 import (APIC, ID3, TALB, TCON, TIT2, TPE1, TRCK,
                                 ID3NoHeaderError)
    except ImportError:
        import os
        if os.environ.get("PBS_ALLOW_NO_ID3") == "1":  # test sandbox only
            return
        raise
    try:
        tags = ID3(str(mp3_path))
    except ID3NoHeaderError:
        tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TALB(encoding=3, text=album if album else title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    tags.add(TCON(encoding=3, text=genre))
    if track:
        tags.add(TRCK(encoding=3, text=str(track)))
    if cover_path and Path(cover_path).exists():
        data = Path(cover_path).read_bytes()
        mime = "image/png" if data[:8].startswith(b"\x89PNG") else "image/jpeg"
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    tags.save(str(mp3_path))
