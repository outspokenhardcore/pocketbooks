"""Public-domain sample library: cleaning, structure, and PDF generation.

All tests run offline — the network fetch is the only part that is stubbed.
"""
import json

import pytest

SOURCE = """\
The Project Gutenberg eBook of A Test Voyage

This eBook is for the use of anyone anywhere at no cost.

*** START OF THE PROJECT GUTENBERG EBOOK A TEST VOYAGE ***

A Test Voyage

by A. Writer


Contents

 I The Signal
 II The Chase
 Epilogue


BOOK ONE
THE OUTWARD RUN


I.
THE SIGNAL


The lamp flickered twice across the water and then
went dark for a long moment.

He counted the seconds under his breath.


II.
THE CHASE


They ran north with the tide behind them.


Epilogue


Years later he still kept the chart.

*** END OF THE PROJECT GUTENBERG EBOOK A TEST VOYAGE ***

This license applies only to this electronic work.
"""


def _sections(text=SOURCE):
    from app import samples
    return samples.split_sections(samples.strip_distribution_boilerplate(text))


def test_strip_distribution_boilerplate_keeps_only_the_work():
    from app import samples
    cleaned = samples.strip_distribution_boilerplate(SOURCE)
    assert "Project Gutenberg" not in cleaned
    assert "license applies" not in cleaned
    assert "The lamp flickered twice" in cleaned


def test_strip_distribution_boilerplate_removes_markup():
    from app import samples
    cleaned = samples.strip_distribution_boilerplate(
        "[Illustration: a ship]\nHe was _very_ tired.\n")
    assert "[Illustration" not in cleaned
    assert "He was very tired." in cleaned


def test_split_sections_drops_front_matter_and_contents():
    body = " ".join(p for s in _sections() for p in s.paragraphs)
    assert "by A. Writer" not in body
    assert "Contents" not in body
    assert body.startswith("The lamp flickered twice")


def test_split_sections_finds_parts_chapters_and_epilogue():
    sections = _sections()
    assert [s.kind for s in sections] == ["part", "chapter", "chapter", "chapter"]
    assert sections[0].display_title == "Book One: The Outward Run"
    assert [s.display_title for s in sections[1:]] == [
        "Chapter I: The Signal", "Chapter II: The Chase", "Epilogue"]


def test_split_sections_joins_wrapped_lines_into_paragraphs():
    signal = _sections()[1]
    assert signal.paragraphs == [
        "The lamp flickered twice across the water and then went dark for a "
        "long moment.",
        "He counted the seconds under his breath.",
    ]


def test_chapter_groups_keep_a_part_with_the_chapter_that_follows():
    from app import samples
    groups = samples.chapter_groups(_sections())
    assert [len(g) for g in groups] == [2, 1, 1]
    assert groups[0][0].kind == "part"


def test_smart_title_only_rewrites_shouted_headings():
    from app import samples
    assert samples.smart_title("THE DEATH OF THE CURATE") == "The Death of the Curate"
    assert samples.smart_title("The Man Who Died") == "The Man Who Died"
    assert samples.smart_title("BOOK II") == "Book II"


def test_cover_theme_is_chosen_per_genre():
    from app import samples
    science = samples.cover_theme("Science fiction")
    spy = samples.cover_theme("Spy thriller")
    assert science != spy
    assert science == samples.cover_theme("SCIENCE FICTION")
    assert samples.cover_theme("Cookery") == samples.cover_theme("")
    for background, accent in (science, spy):
        assert background.startswith("#") and len(background) == 7
        assert accent.startswith("#") and len(accent) == 7


def test_every_catalog_genre_has_its_own_cover_theme():
    from app import samples
    fallback = samples.cover_theme("")
    for book in samples.load_catalog():
        assert samples.cover_theme(book.genre) != fallback, book.slug


def test_catalog_lists_only_public_domain_english_titles():
    from app import samples
    books = samples.load_catalog()
    assert len(books) >= 5
    slugs = {b.slug for b in books}
    assert "the-war-of-the-worlds" in slugs  # shared with the sibling project
    for book in books:
        assert book.first_published < 1930, book.slug
        assert book.rights.get("status") == "Public domain", book.slug
        assert book.rights.get("author_died", 9999) < 1955, book.slug
        assert book.text_url.startswith("https://"), book.slug
        assert book.filename.endswith(".pdf"), book.slug


def test_catalog_json_is_valid_and_has_unique_filenames():
    from app import samples
    raw = json.loads(samples.catalog_path().read_text(encoding="utf-8"))
    names = [b["filename"] for b in raw["books"]]
    assert len(names) == len(set(names))


def test_build_book_writes_a_pdf_with_metadata_and_chapter_marks(tmp_path):
    pytest.importorskip("pymupdf")
    import pymupdf as fitz
    from app import samples

    book = samples.load_catalog()[0]
    path, chapters = samples.build_book(book, tmp_path, source_text=SOURCE)

    assert path.exists() and path.parent == tmp_path
    assert chapters == 3
    doc = fitz.open(str(path))
    try:
        assert doc.metadata["title"] == book.title
        assert doc.metadata["author"] == book.author
        toc = doc.get_toc()
        assert [entry[1] for entry in toc] == [
            "Chapter I: The Signal", "Chapter II: The Chase", "Epilogue"]
        assert all(entry[2] >= 2 for entry in toc)  # page 1 is the title page
        cover = " ".join(doc[0].get_text().split())
        assert book.title in cover
        assert book.author in cover
        assert book.genre.upper() in cover
        assert "across the water" in doc[toc[0][2] - 1].get_text()
    finally:
        doc.close()


def test_generated_pdf_feeds_the_normal_preparation_pipeline(env, tmp_path):
    pytest.importorskip("pymupdf")
    from app import pdfproc, samples

    book = samples.load_catalog()[0]
    path, _ = samples.build_book(book, env["lib"], source_text=SOURCE)
    prepared = pdfproc.prepare_job(str(path), tmp_path)

    assert prepared["title"] == book.title
    assert prepared["chapter_titles"][0] == "Chapter I: The Signal"
    assert prepared["cover_path"]
    # The printed heading must be announced once, not read twice.
    spoken = " ".join(chunk[0] for chunk in prepared["chunks"])
    assert spoken.count("The Signal") == 1


def test_build_book_rejects_a_source_without_chapters(tmp_path):
    from app import samples
    book = samples.load_catalog()[0]
    with pytest.raises(ValueError):
        samples.build_book(book, tmp_path, source_text="Just some loose prose.")


def test_download_text_retries_a_truncated_response(monkeypatch):
    """A mirror that truncates one response must not abort the whole build."""
    import http.client
    from app import samples

    calls = {"n": 0}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http.client.IncompleteRead(b"half", 100)
            return "the whole book".encode("utf-8")

    monkeypatch.setattr(samples.time, "sleep", lambda _s: None)
    monkeypatch.setattr(samples.urllib.request, "urlopen",
                        lambda *a, **k: _Response())
    assert samples.download_text("https://example.invalid/book.txt") == "the whole book"
    assert calls["n"] == 2


def test_download_text_gives_up_and_reports_the_url(monkeypatch):
    from app import samples

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise ConnectionResetError("reset by peer")

    monkeypatch.setattr(samples.time, "sleep", lambda _s: None)
    monkeypatch.setattr(samples.urllib.request, "urlopen",
                        lambda *a, **k: _Response())
    with pytest.raises(OSError, match="book.txt"):
        samples.download_text("https://example.invalid/book.txt", attempts=2)


def test_download_text_does_not_retry_a_missing_url(monkeypatch):
    import urllib.error
    from app import samples

    calls = {"n": 0}

    def _open(*args, **kwargs):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://example.invalid/x", 404,
                                     "Not Found", {}, None)

    monkeypatch.setattr(samples.time, "sleep", lambda _s: None)
    monkeypatch.setattr(samples.urllib.request, "urlopen", _open)
    with pytest.raises(urllib.error.HTTPError):
        samples.download_text("https://example.invalid/x")
    assert calls["n"] == 1


def test_main_reports_a_failed_title_without_crashing(tmp_path, monkeypatch, capsys):
    """One unreachable title must not take the other four down with it."""
    from app import samples

    def _boom(url, **kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(samples, "download_text", _boom)
    code = samples.main(["--dest", str(tmp_path), "--only", "the-time-machine"])
    assert code == 1
    assert "connection reset" in capsys.readouterr().err


def test_main_list_does_not_touch_the_network(capsys):
    from app import samples

    def boom(*args, **kwargs):
        raise AssertionError("--list must not download anything")

    original = samples.download_text
    samples.download_text = boom
    try:
        assert samples.main(["--list"]) == 0
    finally:
        samples.download_text = original
    out = capsys.readouterr().out
    assert "the-war-of-the-worlds" in out


def test_main_builds_into_dest_without_network_when_cached(tmp_path, monkeypatch):
    pytest.importorskip("pymupdf")
    from app import samples

    monkeypatch.setattr(samples, "download_text", lambda url, **kw: SOURCE)
    code = samples.main(["--dest", str(tmp_path), "--only", "the-time-machine"])
    assert code == 0
    assert (tmp_path / "The Time Machine.pdf").exists()

    # A second run must not rebuild an existing title.
    monkeypatch.setattr(samples, "download_text", lambda url, **kw: pytest.fail(
        "existing samples must not be downloaded again"))
    assert samples.main(["--dest", str(tmp_path), "--only", "the-time-machine"]) == 0
