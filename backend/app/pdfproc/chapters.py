"""Chapter segmentation from the PDF's embedded table of contents (pure
logic — fitz-free so it is unit-testable everywhere).

Input: per-page texts + TOC entries [(level, title, page1based), ...].
Output: [(chapter_title, chapter_text), ...]
Page granularity: a chapter starting mid-page begins on its start page.
"""
from __future__ import annotations

import re

FRONT_MATTER_MIN_CHARS = 500

# Non-narrative sections that are tedious as audio (the Dramatis Personae in
# particular is a semicolon list with no sentence breaks). Matched against the
# normalized TOC title — exact for the set, prefix for the tuple.
_FRONT_MATTER_EXACT = {
    "title page", "half title", "cover", "copyright", "copyright page",
    "dedication", "acknowledgment", "acknowledgments", "acknowledgement",
    "acknowledgements", "dramatis personae", "cast of characters",
    "contents", "table of contents", "epigraph", "colophon", "newsletter",
    "map", "maps", "star map",
}
_FRONT_MATTER_PREFIXES = (
    "also by", "by the same author", "other books", "praise for",
    "about the author", "about the publisher",
)

_FRONT_PAGE_MARKERS = (
    "acknowledgment", "acknowledgement", "also by", "other books by",
    "the covert-one novels", "the covert one novels", "praise for",
    "the critics on", "copyright", "all rights reserved", "isbn",
    "first published", "printed in", "published by", "published in",
    "this book is a work of fiction", "digitized by the internet archive",
    "library of congress", "cover art by", "distributed by",
    "note: if you purchased this book", "note if you purchased this book",
    "national association for visually handicapped",
)

_START_RE = re.compile(
    r"^(?:prolog(?:ue)?|chapter\s+(?:one|1|i)\b|part\s+(?:one|1|i)\b)",
    re.I,
)


def _is_front_matter(title: str) -> bool:
    norm = re.sub(r"[^a-z ]", "", title.lower()).strip()
    norm = re.sub(r"\s+", " ", norm)
    return norm in _FRONT_MATTER_EXACT or norm.startswith(_FRONT_MATTER_PREFIXES)


def _page_lines(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip()
            for line in text.splitlines() if line.strip()]


def _is_front_page(text: str) -> tuple[bool, str | None]:
    lines = _page_lines(text)
    if not lines:
        return False, None
    lead = " ".join(lines[:8]).casefold()
    for marker in _FRONT_PAGE_MARKERS:
        if marker in lead:
            return True, marker
    first = lines[0].casefold()
    if first.startswith(("dedicated to ", "dedication")):
        return True, "dedication"
    if len(text.strip()) < 350 and first.startswith("for "):
        return True, "dedication"
    # Short all-caps pages in the opening are title/series divider pages.
    letters = [c for c in text if c.isalpha()]
    words = re.findall(r"[A-Za-zÀ-ÿ]+", text)
    if letters and len(words) <= 18:
        upper_share = sum(c.isupper() for c in letters) / len(letters)
        if upper_share >= 0.8:
            return True, "title page"
    return False, None


def _explicit_story_marker(text: str) -> str | None:
    for line in _page_lines(text)[:8]:
        normalized = re.sub(r"[^A-Za-z0-9 ]", "", line).strip()
        if _START_RE.match(normalized):
            return line
    return None


def _looks_narrative(text: str) -> bool:
    if len(text.strip()) < 500:
        return False
    is_front, _ = _is_front_page(text)
    if is_front:
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ']+", text)
    if len(words) < 90:
        return False
    # Maps and OCR-noise pages often contain hundreds of isolated letters and
    # labels but no real prose. Reject them before looking at punctuation.
    if sum(len(word.strip("'")) <= 1 for word in words) / len(words) > 0.2:
        return False
    letters = [c for c in text if c.isalpha()]
    lower_share = (sum(c.islower() for c in letters) / len(letters)) if letters else 0
    sentence_marks = len(re.findall(r"[.!?][\"'’”)]?(?:\s|$)", text))
    return lower_share >= 0.55 and sentence_marks >= 3


def detect_story_start(pages: list[str]) -> dict:
    """Find the first narrative page when a PDF has no usable TOC.

    Returns a 1-based page number plus confidence/reason diagnostics. Explicit
    Prologue/Chapter markers win; otherwise the first sustained two-page prose
    run after the last confidently identified front-matter page is selected.
    """
    if not pages:
        return {"story_start_page": 1, "confidence": 0.0,
                "reason": "empty document", "skipped_pages": []}

    scan_limit = min(len(pages), max(20, min(60, int(len(pages) * 0.15))))
    front: dict[int, str] = {}
    publication_anchor = -1
    for index, page in enumerate(pages[:scan_limit]):
        is_front, reason = _is_front_page(page)
        if is_front and reason:
            front[index] = reason
            if reason in {
                "copyright", "all rights reserved", "first published",
                "printed in", "published by", "published in",
                "this book is a work of fiction",
                "note: if you purchased this book",
                "note if you purchased this book",
            }:
                publication_anchor = index

    # Start after the publication/legal page where possible. Walk forwards and
    # return the *first* convincing story start. Looking for the globally last
    # front-like page is unsafe because later PART/BOOK divider pages can occur
    # after an actual prologue.
    search_start = publication_anchor + 1
    explicit: tuple[int, str] | None = None
    for index in range(search_start, scan_limit):
        marker = _explicit_story_marker(pages[index])
        if marker:
            explicit = (index, marker)
            break

    # OCR can destroy chapter headings. Require two consecutive prose-heavy
    # pages so advertisements, acknowledgments, and isolated legal prose don't
    # become the beginning of the audiobook.
    narrative_index: int | None = None
    for index in range(search_start, scan_limit):
        if index + 1 >= scan_limit:
            continue
        if _looks_narrative(pages[index]) and _looks_narrative(pages[index + 1]):
            narrative_index = index
            break

    choices = [i for i in (explicit[0] if explicit else None, narrative_index)
               if i is not None]
    if choices:
        index = min(choices)
        use_explicit = explicit is not None and explicit[0] == index
        confidence = 0.99 if use_explicit else (0.9 if publication_anchor >= 0 else 0.75)
        reason = (f"explicit story marker: {explicit[1]}" if use_explicit
                  else "sustained narrative text after front matter")
        return {
            "story_start_page": index + 1,
            "confidence": confidence,
            "reason": reason,
            "skipped_pages": [
                {"page": i + 1, "reason": front.get(i, "before story start")}
                for i in range(index)
            ],
        }

    return {"story_start_page": 1, "confidence": 0.25,
            "reason": "no reliable story start detected", "skipped_pages": []}


def build_chapters(pages: list[str], toc: list,
                   fallback_title: str = "Book",
                   skip_front_matter: bool = False,
                   story_start_page: int | None = None) -> list[tuple[str, str]]:
    n = len(pages)
    entries = [(int(lvl), str(title).strip(), int(page))
               for lvl, title, page, *_ in toc
               if str(title).strip() and 1 <= int(page) <= n]
    picks: list[tuple[str, int]] = []
    if entries:
        min_lvl = min(e[0] for e in entries)
        top = [e for e in entries if e[0] == min_lvl]
        if len(top) < 2:  # a single root (e.g. "Contents"): use its children
            top = [e for e in entries if e[0] > min_lvl] or entries
        seen: set[int] = set()
        for _, title, page in sorted(top, key=lambda e: e[2]):
            if page in seen:
                continue
            seen.add(page)
            picks.append((title, page))
    if not picks:
        start = 0
        if skip_front_matter and story_start_page is not None:
            start = max(0, min(len(pages), int(story_start_page) - 1))
        return [(fallback_title, "\n".join(pages[start:]))]

    drop_front = skip_front_matter
    if skip_front_matter:
        narrative = [p for p in picks if not _is_front_matter(p[0])]
        # only honor the skip if something narrative is left — otherwise the
        # whole TOC looked like front matter and we'd produce an empty book
        if narrative:
            picks = narrative
        else:
            drop_front = False

    chapters: list[tuple[str, str]] = []
    first_page = picks[0][1]
    if first_page > 1 and not drop_front:
        front = "\n".join(pages[:first_page - 1])
        if len(front.strip()) >= FRONT_MATTER_MIN_CHARS:
            chapters.append(("Front Matter", front))
    for i, (title, page) in enumerate(picks):
        end = picks[i + 1][1] - 1 if i + 1 < len(picks) else n
        chapters.append((title, "\n".join(pages[page - 1:end])))
    result = [(t, x) for t, x in chapters if x.strip()]
    return result or [(fallback_title, "\n".join(pages))]
