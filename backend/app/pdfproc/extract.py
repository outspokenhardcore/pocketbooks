"""PDF text + cover extraction with PyMuPDF.

Header/footer removal: lines that recur in the same position (first/last
lines of a page) across a large share of pages are boilerplate and dropped,
as are standalone page numbers.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import pymupdf as fitz

_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s+)?[0-9ivxlcdm]+\s*(?:of\s+\d+\s*)?$", re.I)
_HEADER_BAND = 0.09
_FOOTER_BAND = 0.91
_BOILERPLATE_SHARE = 0.25
_BOILERPLATE_MIN_PAGES = 6


def _normalize_line(line: str) -> str:
    """Key for recurrence detection: digits collapsed ('Page 12' == 'Page 13')."""
    line = re.sub(r"\d+", "#", line.strip().casefold())
    line = re.sub(r"\s+", " ", line)
    return re.sub(r"\s*([|:·•-])\s*", r" \1 ", line).strip()


def _fingerprints(text: str) -> set[str]:
    """Stable variants for OCR-damaged running heads.

    Scans often corrupt the page-number glyph into a different short token on
    every page (``Wie | David Weber``, ``£22 | David Weber``). Dropping one
    leading/trailing token still exposes the recurring author/title portion.
    """
    full = _normalize_line(text)
    tokens = full.split()
    out = {full} if full else set()
    if len(tokens) >= 3:
        for variant in (tokens[1:], tokens[:-1]):
            value = " ".join(variant).strip(" |:·•-")
            if len(value) >= 5 and len(re.findall(r"[a-z]", value)) >= 4:
                out.add(value)
    return out


def _zone(y0: float, y1: float, height: float) -> str | None:
    if height <= 0:
        return None
    if y1 / height <= _HEADER_BAND:
        return "header"
    if y0 / height >= _FOOTER_BAND:
        return "footer"
    return None


def _is_narrative_heading(text: str) -> bool:
    norm = re.sub(r"[^a-z0-9 ]", " ", text.casefold())
    norm = re.sub(r"\s+", " ", norm).strip()
    return bool(re.match(
        r"^(?:chapter|part|book)\s+(?:\d+|[ivxlcdm]+|one|two|three)\b|^prolog(?:ue)?\b",
        norm,
    ))


def _is_page_number(text: str) -> bool:
    compact = re.sub(r"\s+", " ", text).strip()
    return bool(_PAGE_NUM_RE.fullmatch(compact))


def _short_edge_block_with_page_number(text: str) -> bool:
    """Running heads often combine the page number with author/title text."""
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) > 100 or _is_narrative_heading(compact):
        return False
    number = r"(?:\d+|[ivxlcdm]+)"
    return bool(re.match(rf"^{number}(?:\s|[|:·•-])", compact, re.I)
                or re.search(rf"(?:\s|[|:·•-]){number}$", compact, re.I))


def extract_text(pdf_path: str | Path) -> str:
    return "\n".join(extract_pages(pdf_path))


def get_toc(pdf_path: str | Path) -> list:
    """Embedded table of contents: [[level, title, page1based], ...]."""
    try:
        doc = fitz.open(str(pdf_path))
        toc = doc.get_toc(simple=True) or []
        doc.close()
        return toc
    except Exception:  # noqa: BLE001
        return []


def extract_pages_with_report(pdf_path: str | Path) -> tuple[list[str], dict]:
    """Extract page text while removing recurring geometric page furniture.

    Only blocks physically inside the top/bottom page bands are candidates.
    Repetition is measured separately for odd/even pages, which handles books
    that alternate author and title running heads.
    """
    doc = fitz.open(str(pdf_path))
    try:
        pages: list[list[dict]] = []
        counts: Counter = Counter()
        parity_totals = Counter()

        for page_index, page in enumerate(doc):
            parity = page_index % 2
            parity_totals[parity] += 1
            height = float(page.rect.height)
            records: list[dict] = []
            seen_on_page: set[tuple] = set()
            for block in page.get_text("blocks", sort=True):
                x0, y0, x1, y1, raw = block[:5]
                text = "\n".join(line.rstrip() for line in str(raw).splitlines()).strip()
                if not text:
                    continue
                edge = _zone(float(y0), float(y1), height)
                signature = _normalize_line(text)
                keys = ({(edge, parity, fp) for fp in _fingerprints(text)}
                        if edge else set())
                records.append({"text": text, "zone": edge, "keys": keys,
                                "signature": signature})
                if not _is_narrative_heading(text):
                    for key in keys - seen_on_page:
                        counts[key] += 1
                        seen_on_page.add(key)
            pages.append(records)

        repeated: set[tuple] = set()
        for key, count in counts.items():
            _, parity, signature = key
            threshold = max(
                _BOILERPLATE_MIN_PAGES,
                math.ceil(parity_totals[parity] * _BOILERPLATE_SHARE),
            )
            if signature and count >= threshold:
                repeated.add(key)

        removed_headers: Counter = Counter()
        removed_page_numbers = 0
        out_pages: list[str] = []
        for records in pages:
            kept: list[str] = []
            for rec in records:
                text = rec["text"]
                if rec["zone"] and _is_page_number(text):
                    removed_page_numbers += 1
                    continue
                if rec["zone"] and (_short_edge_block_with_page_number(text)
                                    or bool(rec["keys"] & repeated)):
                    removed_headers[rec["signature"]] += 1
                    continue
                kept.append(text)
            out_pages.append("\n".join(kept))

        report = {
            "removed_headers": dict(removed_headers.most_common()),
            "removed_page_numbers": removed_page_numbers,
            "header_band": _HEADER_BAND,
            "footer_band": 1.0 - _FOOTER_BAND,
        }
        return out_pages, report
    finally:
        doc.close()


def extract_pages(pdf_path: str | Path) -> list[str]:
    """Compatibility wrapper returning only the cleaned per-page text."""
    pages, _ = extract_pages_with_report(pdf_path)
    return pages


def extract_cover(pdf_path: str | Path, out_dir: Path) -> str | None:
    """Largest embedded image on page 1, else render page 1 to PNG."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cover = out_dir / "cover.png"
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:  # noqa: BLE001
        return None
    try:
        if doc.page_count == 0:
            return None
        page = doc[0]
        best = None
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                pix = fitz.Pixmap(doc, xref)
            except Exception:  # noqa: BLE001
                continue
            if pix.width * pix.height >= (best.width * best.height if best else 40_000):
                best = pix
        if best is not None:
            if best.colorspace and best.colorspace.n > 3 or best.alpha:
                best = fitz.Pixmap(fitz.csRGB, best)
            best.save(str(cover))
            return str(cover)
        # fallback: render page 1
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        pix.save(str(cover))
        return str(cover)
    except Exception:  # noqa: BLE001
        return None
    finally:
        doc.close()


def pdf_metadata(pdf_path: str | Path) -> dict:
    try:
        doc = fitz.open(str(pdf_path))
        meta = dict(doc.metadata or {})
        doc.close()
        return meta
    except Exception:  # noqa: BLE001
        return {}
