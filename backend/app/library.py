"""PDF library: scan the library folder, give each book a stable id (content
hash), and assemble the listing the iOS app browses.

book_id == sha256 of the PDF bytes. It is stable across renames/moves, so the
pre-clean cache, the audio cache and the listening position all key off it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import config
from .db import DB

# Rough spoken characters per second — only used to show an estimated runtime
# in the library before a book has been generated. ~14 c/s ≈ 165 wpm.
SPEECH_CHARS_PER_S = 14.0

# (path, mtime, size) -> hash, so re-listing the folder doesn't re-hash files.
_HASH_CACHE: dict[tuple[str, float, int], str] = {}
# hash -> last known path, so resolve_pdf is O(1) instead of rescanning 300 PDFs.
_PATH_BY_HASH: dict[str, Path] = {}


def file_hash(path: str | Path) -> str:
    p = Path(path)
    st = p.stat()
    key = (str(p), st.st_mtime, st.st_size)
    cached = _HASH_CACHE.get(key)
    if cached:
        _PATH_BY_HASH[cached] = p
        return cached
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    digest = h.hexdigest()
    _HASH_CACHE[key] = digest
    _PATH_BY_HASH[digest] = p
    return digest


def _preclean_meta(pdf_hash: str) -> Optional[dict]:
    meta_file = config.preclean_dir() / pdf_hash / "meta.json"
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _est_duration_s(pdf_hash: str) -> Optional[float]:
    plan = config.preclean_dir() / pdf_hash / "chunks.json"
    if not plan.exists():
        return None
    try:
        chunks = json.loads(plan.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    chars = sum(len(c[0]) for c in chunks if c and c[0])
    return chars / SPEECH_CHARS_PER_S if chars else None


def cache_status(db: DB, pdf_hash: str, voice_id: Optional[int],
                 fmt: str) -> str:
    """none | cleaned | partial | ready for a given voice/format."""
    if voice_id is not None:
        row = db.get_cache(f"{pdf_hash}:{voice_id}:{fmt}")
        if row:
            return row["state"]  # partial | ready
    if (config.preclean_dir() / pdf_hash / "chunks.json").exists():
        return "cleaned"
    return "none"


def _all_pdfs() -> list[Path]:
    """Every PDF anywhere under the library folder (recursive), sorted."""
    return sorted(config.library_dir().rglob("*.pdf"))


def _book_dict(db: DB, pdf: Path, voice_id: Optional[int], fmt: str) -> dict:
    h = file_hash(pdf)
    meta = _preclean_meta(h) or {}
    prog = db.get_progress(h)
    rel = pdf.relative_to(config.library_dir())
    return {
        "book_id": h,
        "filename": pdf.name,
        "rel_path": rel.as_posix(),
        "folder": rel.parent.as_posix() if rel.parent.as_posix() != "." else "",
        # Display name = the filename stem you chose (e.g. "006 - Red Harvest").
        # Stable before/after conversion; the embedded PDF title (which may drop
        # your leading number) is only used for the M4B tags, not the listing.
        "title": pdf.stem,
        "author": meta.get("author") or "",
        "series": meta.get("series"),
        "has_cover": (config.preclean_dir() / h / "cover.jpg").exists(),
        "est_duration_s": _est_duration_s(h),
        "cache_status": cache_status(db, h, voice_id, fmt),
        "position_s": prog["position_s"] if prog else 0.0,
    }


def list_books(db: DB, voice_id: Optional[int] = None,
               fmt: str = "m4b") -> list[dict]:
    """Flat list of every book in the library (recursive)."""
    out = []
    for pdf in _all_pdfs():
        try:
            out.append(_book_dict(db, pdf, voice_id, fmt))
        except OSError:
            continue
    return out


def browse(db: DB, folder: str = "", q: Optional[str] = None,
           voice_id: Optional[int] = None, fmt: str = "m4b") -> dict:
    """Folder browser + global search.

    With `q`: a flat search across ALL books (title/author/filename/path).
    Otherwise: the contents of `folder` — its immediate subfolders (with a
    recursive book count) and the books directly inside it.
    """
    pdfs = _all_pdfs()
    if q:
        ql = q.lower()
        books = []
        for pdf in pdfs:
            try:
                b = _book_dict(db, pdf, voice_id, fmt)
            except OSError:
                continue
            hay = f"{b['title']} {b['author']} {b['rel_path']}".lower()
            if ql in hay:
                books.append(b)
        return {"folder": "", "query": q, "subfolders": [], "books": books}

    folder = folder.strip("/")
    subcounts: dict[str, int] = {}
    books = []
    for pdf in pdfs:
        rel = pdf.relative_to(config.library_dir()).as_posix()
        if folder:
            if not rel.startswith(folder + "/"):
                continue
            remainder = rel[len(folder) + 1:]
        else:
            remainder = rel
        if "/" in remainder:                       # lives in a subfolder
            sub = remainder.split("/", 1)[0]
            subcounts[sub] = subcounts.get(sub, 0) + 1
        else:                                       # a book directly here
            try:
                books.append(_book_dict(db, pdf, voice_id, fmt))
            except OSError:
                continue
    subfolders = [
        {"name": name,
         "path": f"{folder}/{name}" if folder else name,
         "count": cnt}
        for name, cnt in sorted(subcounts.items())
    ]
    return {"folder": folder, "query": None,
            "subfolders": subfolders, "books": books}


def started_books(db: DB, voice_id: Optional[int] = None,
                  fmt: str = "m4b") -> list[dict]:
    """Books with a saved listening position ("Continue listening"), most
    recently played first.

    Books whose PDF is temporarily missing are still returned as ghost entries
    (pdf_missing=True) so they stay visible in the UI instead of silently
    disappearing. The stored title/author from the progress row are used as
    fallback metadata."""
    rows = db.query(
        "SELECT * FROM progress WHERE position_s > 1 ORDER BY updated_at DESC")
    out = []
    for r in rows:
        pdf = resolve_pdf(r["book_id"])
        if not pdf:
            # Keep the book visible even though the PDF is not currently found.
            # title/author were saved by put_progress; fall back to a short hash.
            out.append({
                "book_id": r["book_id"],
                "filename": "",
                "rel_path": "",
                "folder": "",
                "title": r.get("title") or r["book_id"][:12] + "…",
                "author": r.get("author") or "",
                "series": None,
                "has_cover": False,
                "est_duration_s": r.get("duration_s"),
                "cache_status": "none",
                "position_s": r["position_s"],
                "duration_s": r.get("duration_s"),
                "updated_at": r["updated_at"],
                "pdf_missing": True,
            })
            continue
        try:
            b = _book_dict(db, pdf, voice_id, fmt)
        except OSError:
            continue
        b["duration_s"] = r["duration_s"]
        b["updated_at"] = r["updated_at"]
        b["pdf_missing"] = False
        out.append(b)
    return out


def resolve_pdf(pdf_hash: str) -> Optional[Path]:
    """Map a book_id back to a PDF currently in the library folder (recursive)."""
    cached = _PATH_BY_HASH.get(pdf_hash)
    if cached and cached.exists():
        try:
            if file_hash(cached) == pdf_hash:
                return cached
        except OSError:
            pass
    for pdf in _all_pdfs():
        try:
            if file_hash(pdf) == pdf_hash:
                return pdf
        except OSError:
            continue
    return None


def ensure_cover(pdf_hash: str) -> Optional[Path]:
    """Lazily extract a book's cover on first request (cheap: the page-1 image
    or a render of page 1), independent of full conversion. Cached afterwards."""
    cdir = config.preclean_dir() / pdf_hash
    jpg = cdir / "cover.jpg"
    if jpg.exists():
        return jpg
    pdf = resolve_pdf(pdf_hash)
    if not pdf:
        return None
    try:
        from .pdfproc.extract import extract_cover
        cdir.mkdir(parents=True, exist_ok=True)
        png = extract_cover(str(pdf), cdir)   # writes cover.png
        if png and Path(png).exists():
            import shutil
            shutil.copyfile(png, jpg)
            return jpg
    except Exception:  # noqa: BLE001
        return None
    return None
