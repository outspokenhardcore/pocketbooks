"""Public-domain starter library.

Downloads the plain-text source of a small, curated set of public-domain
novels, strips the distributor's boilerplate, and renders clean, text-layer
PDFs with an embedded table of contents into the PocketBooks library folder.

The generated PDFs are what a first-time user converts: no scans, no OCR
noise, correct chapter marks, and a readable cover page.

    python -m app.samples            # build all samples into the library
    python -m app.samples --list     # show the catalog
    python -m app.samples --dest DIR # build somewhere else

Only works the user may legally use are included; see ``samples/catalog.json``
and ``samples/README.md`` for the per-title provenance and rights record.
"""
from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import config

USER_AGENT = (
    "PocketBooks-sample-fetcher/1.0 "
    "(+https://github.com/outspokenhardcore/pocketbooks)"
)
DOWNLOAD_TIMEOUT_S = 60
DOWNLOAD_ATTEMPTS = 4
RETRY_BACKOFF_S = 2

PAGE_RECT = (0, 0, 432, 648)  # 6 x 9 inch trade paperback
PAGE_MARGIN = 54.0

# --- Catalog -----------------------------------------------------------------


@dataclass(frozen=True)
class SampleBook:
    slug: str
    filename: str
    title: str
    author: str
    first_published: int
    genre: str
    blurb: str
    text_url: str
    page_url: str
    rights: dict = field(default_factory=dict)

    @property
    def rights_line(self) -> str:
        status = self.rights.get("status", "Public domain")
        died = self.rights.get("author_died")
        return f"{status} — author died {died}" if died else status


def catalog_path() -> Path:
    """Repository ``samples/catalog.json`` (backend/app/ -> repo root)."""
    return Path(__file__).resolve().parents[2] / "samples" / "catalog.json"


def load_catalog(path: str | Path | None = None) -> list[SampleBook]:
    raw = json.loads(Path(path or catalog_path()).read_text(encoding="utf-8"))
    books = []
    for entry in raw.get("books", []):
        source = entry.get("source", {})
        books.append(SampleBook(
            slug=entry["slug"],
            filename=entry["filename"],
            title=entry["title"],
            author=entry["author"],
            first_published=int(entry["first_published"]),
            genre=entry.get("genre", ""),
            blurb=entry.get("blurb", ""),
            text_url=source["text_url"],
            page_url=source.get("page_url", ""),
            rights=entry.get("rights", {}),
        ))
    return books


def collection_name(path: str | Path | None = None) -> str:
    raw = json.loads(Path(path or catalog_path()).read_text(encoding="utf-8"))
    return raw.get("collection") or "Public Domain Samples"


# --- Source text cleaning -----------------------------------------------------

_START_MARK = re.compile(r"^\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG"
                         r" EBOOK.*\*\*\*\s*$", re.I | re.M)
_END_MARK = re.compile(r"^\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG"
                       r" EBOOK.*\*\*\*\s*$", re.I | re.M)
_ILLUSTRATION = re.compile(r"^\s*\[(?:Illustration|Image)[^\]]*\]\s*$", re.M)
_EMPHASIS = re.compile(r"_([^_\n]{1,200}?)_")
_TRANSCRIBER = re.compile(r"^\s*(?:\[?Transcriber['\u2019]s Note)", re.I)


def strip_distribution_boilerplate(raw: str) -> str:
    """Keep only the work itself.

    The distributor wraps every text in a header and a license footer. Both are
    removed so the generated PDF carries nothing but the public-domain work and
    the title page PocketBooks adds.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    start = _START_MARK.search(text)
    if start:
        text = text[start.end():]
    end = _END_MARK.search(text)
    if end:
        text = text[:end.start()]
    text = _ILLUSTRATION.sub("", text)
    text = _EMPHASIS.sub(r"\1", text)
    return text.strip("\n")


# --- Structure ----------------------------------------------------------------

_ROMAN = r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_WORD_NUM = (r"ONE|TWO|THREE|FOUR|FIVE|SIX|SEVEN|EIGHT|NINE|TEN|ELEVEN|TWELVE|"
             r"THIRTEEN|FOURTEEN|FIFTEEN|SIXTEEN|SEVENTEEN|EIGHTEEN|NINETEEN|"
             r"TWENTY")
_COUNTER = rf"(?:{_ROMAN}|\d{{1,3}}|{_WORD_NUM})"

_PART_RE = re.compile(rf"^(?:BOOK|PART|VOLUME)\s+{_COUNTER}\.?$", re.I)
_CHAPTER_RE = re.compile(rf"^(?:CHAPTER|CHAP\.)\s*{_COUNTER}\.?$", re.I)
_COUNTER_ONLY_RE = re.compile(rf"^(?:{_ROMAN}|\d{{1,3}})\.$")
_SPECIAL_RE = re.compile(r"^(?:PROLOGUE|EPILOGUE|THE EPILOGUE)\.?$", re.I)

_MINOR_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "the", "to", "up", "upon",
    "with", "within",
}


@dataclass
class Section:
    kind: str  # "part" | "chapter"
    heading: str
    title: str
    paragraphs: list[str] = field(default_factory=list)

    @property
    def display_title(self) -> str:
        if self.title and self.heading:
            return f"{self.heading}: {self.title}"
        return self.title or self.heading


def smart_title(text: str) -> str:
    """Title-case a shouted heading, leave mixed-case headings alone."""
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.8:
        return text.strip()
    words = text.strip().split()
    out = []
    for index, word in enumerate(words):
        bare = word.strip("“”\"'’.,;:!?()[]")
        if bare and re.fullmatch(_ROMAN, bare):
            out.append(word)
            continue
        lowered = word.lower()
        if 0 < index < len(words) - 1 and lowered.strip(".,;:") in _MINOR_WORDS:
            out.append(lowered)
            continue
        out.append(re.sub(r"[A-Za-z][\w’']*",
                          lambda m: m.group(0)[0].upper() + m.group(0)[1:],
                          lowered, count=1))
    return " ".join(out)


def _is_heading(line: str) -> Optional[str]:
    if _PART_RE.match(line):
        return "part"
    if _CHAPTER_RE.match(line) or _COUNTER_ONLY_RE.match(line):
        return "chapter"
    if _SPECIAL_RE.match(line):
        return "special"
    return None


def _canonical_heading(line: str, kind: str) -> str:
    """'II.' and 'CHAPTER II' both become 'Chapter II'; parts keep their word."""
    bare = line.strip().rstrip(".")
    if kind == "chapter":
        counter = re.sub(r"^(?:CHAPTER|CHAP\.)\s*", "", bare, flags=re.I).strip()
        if counter:
            if re.fullmatch(_ROMAN, counter.upper()):
                counter = counter.upper()
            elif not counter.isdigit():
                counter = smart_title(counter)
            return f"Chapter {counter}"
    return smart_title(bare)


def _looks_like_title(line: str, following: str) -> bool:
    if not line or len(line) > 80 or len(line.split()) > 14:
        return False
    if line[0].islower() or line.endswith((",", ";", ":")):
        return False
    letters = [c for c in line if c.isalpha()]
    shouted = bool(letters) and sum(c.isupper() for c in letters) / len(letters) >= 0.8
    if shouted:
        return True
    # A mixed-case line is only a title when it is not a sentence and the
    # prose starts after a blank line.
    return not line.endswith((".", "!", "?")) and not following.strip()


def split_sections(text: str) -> list[Section]:
    """Split a cleaned source text into parts and chapters.

    Front matter before the first numbered chapter (title page, contents,
    transcriber notes, dedications) is dropped: it is noise in an audiobook.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    stripped = [line.strip() for line in lines]

    start = None
    for index, line in enumerate(stripped):
        kind = _is_heading(line)
        if kind in {"part", "chapter"}:
            start = index
            break
    if start is None:
        start = 0

    sections: list[Section] = []
    pending_part: Optional[Section] = None
    buffer: list[str] = []

    def flush() -> None:
        if not sections:
            buffer.clear()
            return
        paragraph = " ".join(part.strip() for part in buffer if part.strip())
        if paragraph:
            sections[-1].paragraphs.append(re.sub(r"\s+", " ", paragraph))
        buffer.clear()

    index = start
    while index < len(lines):
        line = stripped[index]
        kind = _is_heading(line) if (index == start or not stripped[index - 1]) else None
        if kind:
            flush()
            heading = _canonical_heading(line, kind)
            title = ""
            look = index + 1
            while look < min(index + 3, len(lines)) and not stripped[look]:
                look += 1
            if look < len(lines):
                following = stripped[look + 1] if look + 1 < len(lines) else ""
                if stripped[look] \
                        and _is_heading(stripped[look]) not in {"part", "chapter"} \
                        and _looks_like_title(stripped[look], following):
                    title = smart_title(stripped[look].rstrip("."))
                    index = look
            section = Section(kind="part" if kind == "part" else "chapter",
                              heading=heading, title=title)
            if section.kind == "part":
                pending_part = section
            else:
                if pending_part is not None:
                    section.paragraphs = []
                    sections.append(pending_part)
                    pending_part = None
                sections.append(section)
            index += 1
            continue
        if not line:
            flush()
        elif _TRANSCRIBER.match(line):
            flush()
        else:
            buffer.append(line)
        index += 1
    flush()
    return [s for s in sections if s.kind == "part" or s.paragraphs]


def chapter_groups(sections: Sequence[Section]) -> list[list[Section]]:
    """Group each part heading onto the page of the chapter that follows it."""
    groups: list[list[Section]] = []
    pending: list[Section] = []
    for section in sections:
        if section.kind == "part":
            pending.append(section)
            continue
        groups.append(pending + [section])
        pending = []
    if pending:
        groups.append(pending)
    return groups


# --- PDF rendering -------------------------------------------------------------

_CSS = """
body { font-family: serif; font-size: 11pt; line-height: 1.45; }
p { margin: 0 0 0.35em 0; text-indent: 1.2em; text-align: justify; }
p.first { text-indent: 0; }
h1 { font-size: 17pt; text-align: center; margin: 0 0 0.3em 0; }
h2 { font-size: 13pt; text-align: center; margin: 0 0 1.4em 0;
     font-weight: normal; }
h3 { font-size: 11pt; text-align: center; letter-spacing: 2px;
     margin: 0 0 1.6em 0; font-weight: normal; }
"""

# Page 1 doubles as the book cover: the app renders it as the library
# thumbnail, so it is set in large white type over a flat genre colour rather
# than as a plain typographic title page.
_COVER_CSS = """
body {{ font-family: serif; color: #F5F2EC; }}
p {{ text-align: center; text-indent: 0; margin: 0 0 0.3em 0; }}
.book {{ font-size: 33pt; line-height: 1.15; }}
.author {{ font-size: 15pt; color: {accent}; }}
.meta {{ font-size: 10pt; color: #B9B3A8; }}
.genre {{ font-size: 10pt; color: {accent}; letter-spacing: 3px; }}
"""

# genre -> (background, accent)
_COVER_THEMES = {
    "science fiction": ("#0E1A2F", "#5EEAD4"),
    "spy thriller": ("#241610", "#E8B059"),
}
_COVER_FALLBACK = ("#1C1C1C", "#C9C3B6")


def cover_theme(genre: str) -> tuple[str, str]:
    return _COVER_THEMES.get(genre.strip().lower(), _COVER_FALLBACK)


def _hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _title_page_html(book: SampleBook) -> str:
    esc = html.escape
    spacer = '<p>&nbsp;</p>'
    return f"""<div>
{spacer * 3}
<p class="genre">{esc(book.genre.upper())}</p>
{spacer}
<p class="book"><b>{esc(book.title)}</b></p>
{spacer}
<p class="author">{esc(book.author)}</p>
{spacer * 4}
<p class="meta">First published {book.first_published}</p>
<p class="meta">{esc(book.rights_line)}</p>
{spacer}
<p class="meta">Prepared as a PocketBooks sample</p>
</div>"""


def _paint_cover(page, fitz, genre: str) -> None:
    """Flat colour field under the title text, with accent bars in the margins."""
    background, accent = cover_theme(genre)
    page.draw_rect(page.rect, color=None, fill=_hex_to_rgb(background),
                   overlay=False)
    rgb = _hex_to_rgb(accent)
    width, height = page.rect.width, page.rect.height
    # Drawn on top: both bars sit inside the page margin, clear of the text.
    page.draw_rect(fitz.Rect(0, 0, width, 10), color=None, fill=rgb)
    page.draw_rect(fitz.Rect(0, height - 10, width, height), color=None,
                   fill=rgb)


def _group_html(group: Sequence[Section]) -> str:
    esc = html.escape
    parts: list[str] = []
    for section in group:
        if section.kind == "part":
            parts.append(f"<h3>{esc(section.heading)}</h3>")
            if section.title:
                parts.append(f"<h3>{esc(section.title)}</h3>")
            continue
        parts.append(f"<h1>{esc(section.heading)}</h1>")
        parts.append(f"<h2>{esc(section.title) if section.title else '&nbsp;'}</h2>")
        for number, paragraph in enumerate(section.paragraphs):
            css = ' class="first"' if number == 0 else ""
            parts.append(f"<p{css}>{esc(paragraph)}</p>")
    return "\n".join(parts)


def _write_story(writer, fitz, story) -> int:
    """Flow one story through as many pages as it needs; return page count."""
    rect = fitz.Rect(*PAGE_RECT)
    where = rect + (PAGE_MARGIN, PAGE_MARGIN, -PAGE_MARGIN, -PAGE_MARGIN)
    pages = 0
    more = True
    while more:
        device = writer.begin_page(rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
        pages += 1
    return pages


def build_pdf(book: SampleBook, sections: Sequence[Section],
              out_path: str | Path) -> Path:
    import fitz  # PyMuPDF; imported lazily so --list works without it

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".pdf.part")

    groups = chapter_groups(sections)
    toc: list[list] = []
    cover_css = _COVER_CSS.format(accent=cover_theme(book.genre)[1])
    writer = fitz.DocumentWriter(str(tmp))
    try:
        page = 1
        page += _write_story(
            writer, fitz,
            fitz.Story(html=_title_page_html(book), user_css=cover_css))
        for group in groups:
            chapter = next((s for s in group if s.kind == "chapter"), group[0])
            toc.append([1, chapter.display_title, page])
            page += _write_story(
                writer, fitz, fitz.Story(html=_group_html(group), user_css=_CSS))
    finally:
        writer.close()

    doc = fitz.open(str(tmp))
    try:
        doc.set_metadata({
            "title": book.title,
            "author": book.author,
            "subject": book.genre,
            "keywords": f"public domain, {book.first_published}",
            "producer": "PocketBooks sample builder",
        })
        if toc:
            doc.set_toc(toc)
        if doc.page_count:
            _paint_cover(doc[0], fitz, book.genre)
        doc.save(str(out), garbage=4, deflate=True)
    finally:
        doc.close()
    tmp.unlink(missing_ok=True)
    return out


# --- Fetch + build -------------------------------------------------------------


def download_text(url: str, timeout: int = DOWNLOAD_TIMEOUT_S,
                  attempts: int = DOWNLOAD_ATTEMPTS) -> str:
    """Fetch a source text, retrying the transient failures.

    Large plain-text downloads from a public mirror fail often enough that a
    single hiccup should not abort a five-book build: connections get reset and
    responses arrive truncated (``IncompleteRead``). Both are worth retrying;
    a 404 is not.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            if not payload:
                raise OSError("empty response")
            return payload.decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise  # the URL is wrong; retrying cannot help
        except (urllib.error.URLError, http.client.HTTPException,
                OSError) as exc:
            last = exc
            if attempt < attempts:
                delay = RETRY_BACKOFF_S * attempt
                print(f"    retry {attempt}/{attempts - 1} after {exc} "
                      f"({delay}s)", file=sys.stderr)
                time.sleep(delay)
    raise OSError(f"{url}: giving up after {attempts} attempts ({last})")


def build_book(book: SampleBook, dest_dir: str | Path, *,
               source_text: str | None = None) -> tuple[Path, int]:
    text = source_text if source_text is not None else download_text(book.text_url)
    sections = split_sections(strip_distribution_boilerplate(text))
    chapters = [s for s in sections if s.kind == "chapter"]
    if not chapters:
        raise ValueError(f"no chapters found in source for {book.slug}")
    path = build_pdf(book, sections, Path(dest_dir) / book.filename)
    return path, len(chapters)


def default_dest() -> Path:
    return config.library_dir() / collection_name()


def _print_catalog(books: Iterable[SampleBook]) -> None:
    for book in books:
        print(f"{book.slug}\n    {book.title} — {book.author} "
              f"({book.first_published}, {book.genre})\n"
              f"    {book.blurb}\n    {book.rights_line}\n    {book.page_url}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.samples",
        description="Build the public-domain sample library as PDFs.")
    parser.add_argument("--dest", default=None,
                        help="target folder (default: the PocketBooks library)")
    parser.add_argument("--only", action="append", default=None, metavar="SLUG",
                        help="build a single title; repeatable")
    parser.add_argument("--list", action="store_true",
                        help="show the catalog and exit")
    parser.add_argument("--force", action="store_true",
                        help="rebuild titles that already exist")
    args = parser.parse_args(argv)

    books = load_catalog()
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {b.slug for b in books}
        if unknown:
            parser.error(f"unknown title(s): {', '.join(sorted(unknown))}")
        books = [b for b in books if b.slug in wanted]
    if args.list:
        _print_catalog(books)
        return 0

    dest = Path(args.dest).expanduser() if args.dest else default_dest()
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Building {len(books)} public-domain sample(s) into {dest}")

    failures = 0
    for book in books:
        target = dest / book.filename
        if target.exists() and not args.force:
            print(f"  = {book.filename} (already there, use --force to rebuild)")
            continue
        try:
            path, chapters = build_book(book, dest)
        except (urllib.error.URLError, http.client.HTTPException,
                OSError, ValueError) as exc:
            failures += 1
            print(f"  ! {book.filename}: {exc}", file=sys.stderr)
            continue
        size_mb = path.stat().st_size / 1_000_000
        print(f"  + {book.filename} ({chapters} chapters, {size_mb:.1f} MB)")

    if failures:
        print(f"{failures} title(s) failed. Re-run to retry.", file=sys.stderr)
        return 1
    print("Done. Open the PocketBooks app and pick a book to start listening.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
