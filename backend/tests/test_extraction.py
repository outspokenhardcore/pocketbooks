"""Layout furniture removal and no-TOC story-start detection."""
from pathlib import Path


def _make_running_head_pdf(path: Path) -> None:
    import pymupdf as fitz

    doc = fitz.open()
    for index in range(24):
        page = doc.new_page(width=400, height=600)
        if index % 2:
            page.insert_text((30, 20), f"{index + 1} | David Weber", fontsize=9)
        else:
            page.insert_text((220, 20), f"BOOK TITLE | {index + 1}", fontsize=9)
        page.insert_textbox(
            fitz.Rect(40, 70, 360, 540),
            ("Honor entered the bridge and studied the display. "
             "The crew waited for her decision. "
             "Nothing in the report explained what had happened. ") * 4,
            fontsize=11,
        )
        page.insert_text((195, 580), str(index + 1), fontsize=9)
    doc.save(path)
    doc.close()


def test_extract_removes_alternating_running_heads_and_page_numbers(tmp_path):
    from app.pdfproc.extract import extract_pages_with_report

    pdf = tmp_path / "running-heads.pdf"
    _make_running_head_pdf(pdf)
    pages, report = extract_pages_with_report(pdf)
    joined = "\n".join(pages).casefold()

    assert "david weber" not in joined
    assert "book title" not in joined
    assert "honor entered the bridge" in joined
    assert report["removed_page_numbers"] == 24
    assert sum(report["removed_headers"].values()) == 24


def test_story_start_explicit_prologue_skips_acknowledgments():
    from app.pdfproc.chapters import detect_story_start

    pages = [
        "ALSO BY ROBERT LUDLUM\nOther Book\nAnother Book",
        "Copyright 2001. All rights reserved. ISBN 1234.",
        "ACKNOWLEDGMENTS\nWe are grateful to many experts for their help. " * 8,
        "PROLOGUE\nBoston, Massachusetts\nThe caretaker heard tires on gravel. "
        "He looked through the window and waited. " * 8,
        "The visitor stepped from the car. Nothing about him seemed unusual. " * 8,
    ]
    result = detect_story_start(pages)
    assert result["story_start_page"] == 4
    assert result["confidence"] > 0.9


def test_story_start_uses_sustained_prose_when_heading_is_ocr_garbage():
    from app.pdfproc.chapters import detect_story_start

    pages = [
        "THE COVERT-ONE NOVELS\nBook One\nBook Two",
        "This book is a work of fiction. Copyright 2004. All rights reserved.",
        "For Barbara, my agent and friend.",
        "PRILItlE\nMarch 5, 1953\nNothing lived on the island. "
        "The storm swept across the frozen sea. " * 20,
        "A ship might have passed the island without seeing it. "
        "The wind and ice erased every trace. " * 20,
    ]
    result = detect_story_start(pages)
    assert result["story_start_page"] == 4
    assert result["confidence"] >= 0.9


def test_explicit_prologue_beats_prose_like_front_page_immediately_before_it():
    from app.pdfproc.chapters import detect_story_start

    pages = [
        "Copyright 2005. All rights reserved. First published in New York.",
        ("National Association for Visually Handicapped. We provide books "
         "for readers with impaired sight. Our services support libraries. ") * 15,
        ("PROLOGUE\nSaturday, September 25\nZimbabwe\nThe last rays of the sun "
         "were gone. Thousands of stars shimmered above the valley. ") * 10,
        "The patrol moved through the darkness and watched the distant road. " * 20,
    ]
    result = detect_story_start(pages)
    assert result["story_start_page"] == 3
    assert result["confidence"] > 0.9


def test_no_toc_build_starts_at_detected_page():
    from app.pdfproc.chapters import build_chapters

    pages = ["Copyright and advertisements", "Acknowledgments", "Real story begins."]
    chapters = build_chapters(
        pages, [], fallback_title="Book", skip_front_matter=True,
        story_start_page=3,
    )
    assert chapters == [("Book", "Real story begins.")]
