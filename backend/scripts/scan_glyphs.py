"""Scan the PDF library for broken-font glyphs (mis-encoded ligatures etc.).

A glyph is "suspicious" when it appears INSIDE a word but is not a normal
letter/digit/accent — e.g. the IPA codepoints some PDF font subsets use for
f-ligatures ("difficult" -> "diɽcult"). Legitimate accented Latin (café, naïve)
is intra-word too, so we only flag characters outside the normal Latin ranges.

Usage:  python -m scripts.scan_glyphs [library_dir]
Prints, per suspicious char: codepoint, name, total count, #books, sample words.
Read-only; touches nothing.
"""
from __future__ import annotations

import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF


def is_normal_text_char(c: str) -> bool:
    """True for characters that legitimately occur inside English/European words."""
    o = ord(c)
    if c in "'’-":            # intra-word apostrophe/hyphen
        return True
    cat = unicodedata.category(c)
    if cat[0] != "L":          # only letters can sit inside a word legitimately
        return False
    # Basic Latin + Latin-1 + Latin Extended-A/B (accents up to U+024F) are fine;
    # beyond that (IPA, phonetic, etc.) inside a word is almost certainly broken.
    return o <= 0x024F


def scan(library: Path, cleaned: bool = False) -> None:
    pdfs = sorted(library.rglob("*.pdf"))
    clean = None
    if cleaned:
        from app.pdfproc.cleaning import clean_text as clean
    print(f"scanning {len(pdfs)} PDFs under {library}"
          f"{' (after clean_text)' if cleaned else ''}\n")

    total = Counter()                         # char -> total intra-word count
    books = defaultdict(set)                  # char -> set of book filenames
    samples = defaultdict(list)               # char -> sample words
    book_hits = Counter()                     # filename -> suspicious char count

    for pdf in pdfs:
        try:
            doc = fitz.open(str(pdf))
        except Exception as e:  # noqa: BLE001
            print(f"  ! cannot open {pdf.name}: {e}")
            continue
        try:
            text = "".join(page.get_text("text") for page in doc)
        finally:
            doc.close()
        if clean is not None:
            text = clean(text)

        # Walk words; flag any suspicious char that has a letter on either side.
        for i, c in enumerate(text):
            if c.isascii() or is_normal_text_char(c):
                continue
            left = text[i - 1] if i > 0 else " "
            right = text[i + 1] if i + 1 < len(text) else " "
            if (left.isalpha() or right.isalpha()):   # sits inside/next to a word
                total[c] += 1
                books[c].add(pdf.name)
                book_hits[pdf.name] += 1
                if len(samples[c]) < 8:
                    # grab the surrounding word
                    s = i
                    while s > 0 and (text[s - 1].isalpha() or text[s - 1] in "'’-" or not text[s - 1].isascii()):
                        s -= 1
                    e = i + 1
                    while e < len(text) and (text[e].isalpha() or text[e] in "'’-" or not text[e].isascii()):
                        e += 1
                    word = text[s:e].strip()
                    if word and word not in samples[c]:
                        samples[c].append(word)

    if not total:
        print("No suspicious intra-word glyphs found. ✓")
        return

    print("=== suspicious glyphs (most frequent first) ===")
    for c, n in total.most_common():
        try:
            name = unicodedata.name(c)
        except ValueError:
            name = "<no name>"
        print(f"\nU+{ord(c):04X} {c!r}  {name}")
        print(f"  intra-word count: {n}  | in {len(books[c])} books")
        print(f"  samples: {', '.join(samples[c])}")

    print("\n=== most affected books ===")
    for name, n in book_hits.most_common(15):
        print(f"  {n:6d}  {name}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--cleaned"]
    cleaned = "--cleaned" in sys.argv
    lib = Path(args[0]) if args else Path.home() / "Audiobooks" / "PDFs"
    scan(lib, cleaned=cleaned)
