"""Sentence-aware chunking (~200-400 chars) preserving paragraph boundaries.

Returns [(chunk_text, ends_paragraph), ...] — the paragraph flag drives a
slightly longer silence gap in the final MP3.
"""
from __future__ import annotations

import re

from .. import config

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


def split_into_chunks(
    text: str,
    max_chars: int = config.CHUNK_MAX_CHARS,
    min_chars: int = config.CHUNK_MIN_CHARS,
) -> list[tuple[str, bool]]:
    chunks: list[tuple[str, bool]] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for para in paragraphs:
        para = para.replace("\n", " ")
        current = ""
        for sentence in _SENTENCE_SPLIT.split(para):
            sentence = sentence.strip()
            if not sentence:
                continue
            if current and len(current) + len(sentence) + 1 > max_chars:
                chunks.append((current, False))
                current = ""
            if len(sentence) > max_chars:
                if current:
                    chunks.append((current, False))
                    current = ""
                for piece in _hard_split(sentence, max_chars):
                    chunks.append((piece, False))
                continue
            current = f"{current} {sentence}".strip()
            if len(current) >= min_chars and len(current) <= max_chars:
                # good size — emit eagerly to stay near target
                if len(current) >= config.CHUNK_TARGET_CHARS:
                    chunks.append((current, False))
                    current = ""
        if current:
            chunks.append((current, False))
        if chunks:
            last_text, _ = chunks[-1]
            chunks[-1] = (last_text, True)  # mark paragraph end
    return chunks


def _hard_split(sentence: str, max_chars: int) -> list[str]:
    """Last resort for monster sentences: split on commas, then spaces."""
    parts: list[str] = []
    current = ""
    for token in re.split(r"(?<=,)\s+", sentence):
        if current and len(current) + len(token) + 1 > max_chars:
            parts.append(current)
            current = ""
        while len(token) > max_chars:  # no commas either: split on space
            cut = token.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            parts.append((current + " " + token[:cut]).strip())
            current, token = "", token[cut:].strip()
        current = f"{current} {token}".strip()
    if current:
        parts.append(current)
    return [p for p in parts if p]
