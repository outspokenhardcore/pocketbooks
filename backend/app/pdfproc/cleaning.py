"""Text cleaning for TTS — no garbage characters may reach the engine.

Extends ~/voice-audiobooks/src/textutils.py and the audiobooks_batch.py
regexes (kept verbatim where they worked).
"""
from __future__ import annotations

import re
import unicodedata

_CHAR_MAP = {
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'",
    # en-dash / minus stay hyphens (ranges like "1939-1945"); em-dash and
    # horizontal bar become a comma so TTS produces an audible pause instead
    # of swallowing the break ("...Lowbacca and-" -> "...Lowbacca and,")
    "–": "-", "−": "-", "—": ", ", "―": ", ",
    "…": "...",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "­": "",   # soft hyphen
    "​": "", "‌": "", "‍": "", "﻿": "",
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
    "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
    # Some PDF font subsets have a broken ToUnicode CMap that maps the f-ligature
    # glyphs to IPA codepoints (e.g. "fish" extracts as "ɹsh"). These IPA letters
    # never occur in normal prose, so mapping them back to ligatures is safe.
    # Deduced from context: ɹ=fiction/film, ɻ=reflect/floor, ʃ=off/different,
    # ɽ=difficult/traffic, ʀ=muffled/ruffles.
    "ɹ": "fi", "ɻ": "fl", "ʃ": "ff", "ɽ": "ffi", "ʀ": "ffl",
    "•": " ", "●": " ", "■": " ",
    "®": "", "™": "",   # trademark marks — don't read them aloud
    # Cyrillic homoglyphs some PDFs substitute for visually identical Latin
    # letters ("Coruscant" -> "Corusсant", "party" -> "раrtу"). Cyrillic never
    # legitimately occurs in this English library, so fold the confusables back.
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "ѕ": "s", "к": "k",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H",
    "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
}
_CHAR_RE = re.compile("|".join(re.escape(k) for k in _CHAR_MAP))


def clean_text(raw: str) -> str:
    # 1. explicit replacements first (NFKC would turn “ into " anyway, but
    #    en/em dashes and bullets need our own mapping)
    text = _CHAR_RE.sub(lambda m: _CHAR_MAP[m.group(0)], raw)
    # 1b. some fonts emit "é" as ©/¨/й inside words: "caf©"->café, "Padm¨"->Padmé.
    #     Only repair when it directly follows a letter, so a standalone © (the
    #     copyright sign in front matter) is left untouched.
    text = re.sub(r"(?<=[A-Za-zÀ-ÿ])[©¨й]", "é", text)
    # 2. unicode normalize (handles remaining ligatures/compat chars)
    text = unicodedata.normalize("NFKC", text)
    # 3. strip control / non-printable chars (keep \n and \t)
    text = "".join(
        ch for ch in text
        if ch in "\n\t" or (unicodedata.category(ch)[0] != "C"))
    # 4. de-hyphenate words split across line breaks: "exam-\nple" -> "example",
    #    but keep the hyphen when the continuation is itself hyphenated
    #    (compound like "state-\nof-the-art")
    text = re.sub(r"(\w)-\n(?=\w+(?:-\w+)+)", r"\1-", text)
    text = re.sub(r"(\w)-\n(?=\w)", r"\1", text)
    # 5. un-wrap hard line breaks inside sentences, keep paragraph breaks
    #    (audiobooks_batch.py regex, fixed: must not eat the 2nd \n of a
    #    paragraph break, hence \n added to the lookbehind class)
    text = re.sub(r"(?<![.!?:\n])\n(?!\n)", " ", text)
    # 6. collapse whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()
