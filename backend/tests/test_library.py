"""Library scan: content hashing, listing, pdf resolution, pre-clean status."""
import json

import pytest

from .conftest import make_pdf


def test_file_hash_is_stable_and_memoized(env):
    from app import library
    pdf = env["lib"] / "a.pdf"
    make_pdf(pdf)
    h1 = library.file_hash(pdf)
    h2 = library.file_hash(pdf)
    assert h1 == h2 and len(h1) == 64
    # Second call should be served from the (path, mtime, size) memo cache.
    st = pdf.stat()
    assert (str(pdf), st.st_mtime, st.st_size) in library._HASH_CACHE


def test_different_content_different_hash(env):
    from app import library
    a = env["lib"] / "a.pdf"
    b = env["lib"] / "b.pdf"
    make_pdf(a, body="One.")
    make_pdf(b, body="Completely different text here.")
    assert library.file_hash(a) != library.file_hash(b)


def test_list_books_and_resolve(db, env):
    from app import library
    pdf = env["lib"] / "The Book.pdf"
    make_pdf(pdf, title="The Book", author="Me")
    books = library.list_books(db)
    assert len(books) == 1
    book = books[0]
    assert book["title"] == "The Book"          # falls back to PDF stem/metadata
    assert book["cache_status"] == "none"        # nothing generated yet
    assert book["position_s"] == 0.0
    # book_id resolves back to the file on disk.
    assert library.resolve_pdf(book["book_id"]) == pdf
    assert library.resolve_pdf("deadbeef") is None


def test_browse_folders_and_counts(db, env):
    from app import library
    (env["lib"] / "SciFi" / "StarWars").mkdir(parents=True)
    (env["lib"] / "Crime").mkdir()
    make_pdf(env["lib"] / "root.pdf", title="Root")
    make_pdf(env["lib"] / "SciFi" / "a.pdf", title="A")
    make_pdf(env["lib"] / "SciFi" / "StarWars" / "b.pdf", title="B")
    make_pdf(env["lib"] / "Crime" / "c.pdf", title="C")

    root = library.browse(db)
    assert [s["name"] for s in root["subfolders"]] == ["Crime", "SciFi"]
    counts = {s["name"]: s["count"] for s in root["subfolders"]}
    assert counts == {"Crime": 1, "SciFi": 2}        # SciFi counts nested too
    assert [b["filename"] for b in root["books"]] == ["root.pdf"]

    scifi = library.browse(db, folder="SciFi")
    assert [s["name"] for s in scifi["subfolders"]] == ["StarWars"]
    # Title falls back to the filename stem until a book is pre-cleaned.
    assert [b["filename"] for b in scifi["books"]] == ["a.pdf"]

    sw = library.browse(db, folder="SciFi/StarWars")
    assert sw["subfolders"] == []
    assert [b["filename"] for b in sw["books"]] == ["b.pdf"]
    assert sw["books"][0]["folder"] == "SciFi/StarWars"


def test_started_books_lists_in_progress(db, env):
    from app import library
    a = env["lib"] / "a.pdf"
    b = env["lib"] / "b.pdf"
    make_pdf(a, title="A")
    make_pdf(b, title="B")
    ha, hb = library.file_hash(a), library.file_hash(b)
    db.set_progress(ha, 120.0, 3600.0, "iphone", updated_at=1000)
    db.set_progress(hb, 300.0, 3600.0, "iphone", updated_at=2000)
    started = library.started_books(db)
    # most recently played first; both included, position carried through
    assert [s["filename"] for s in started] == ["b.pdf", "a.pdf"]
    assert started[0]["position_s"] == 300.0
    # a book with no/near-zero progress is excluded
    db.set_progress(ha, 0.0, None, "iphone", updated_at=3000)
    assert [s["filename"] for s in library.started_books(db)] == ["b.pdf"]


def test_browse_search_is_global(db, env):
    from app import library
    (env["lib"] / "SciFi").mkdir()
    make_pdf(env["lib"] / "SciFi" / "dune.pdf", title="Dune")
    make_pdf(env["lib"] / "other.pdf", title="Something Else")
    res = library.browse(db, q="dune")   # matches filename/path globally
    assert [b["filename"] for b in res["books"]] == ["dune.pdf"]
    assert res["subfolders"] == []


def test_ensure_cover_extracts_lazily(db, env):
    from app import config, library
    pdf = env["lib"] / "z.pdf"
    make_pdf(pdf)
    h = library.file_hash(pdf)
    assert not (config.preclean_dir() / h / "cover.jpg").exists()  # not yet
    cover = library.ensure_cover(h)                                # extracts now
    assert cover is not None and cover.exists()
    assert library.ensure_cover(h) == cover                        # cached 2nd time
    assert library.ensure_cover("deadbeef") is None                # unknown book


def test_resolve_pdf_uses_hash_index(db, env):
    from app import library
    pdf = env["lib"] / "idx.pdf"
    make_pdf(pdf)
    h = library.file_hash(pdf)            # populates the hash->path index
    assert library.resolve_pdf(h) == pdf


def test_cache_status_reflects_preclean(db, env):
    from app import config, library
    pdf = env["lib"] / "x.pdf"
    make_pdf(pdf)
    h = library.file_hash(pdf)
    # Seed a pre-clean cache entry for this hash.
    cdir = config.preclean_dir() / h
    cdir.mkdir(parents=True)
    (cdir / "chunks.json").write_text(json.dumps([["hi", 0, 0]]))
    (cdir / "meta.json").write_text(json.dumps(
        {"title": "X", "author": "Y", "track": None, "series": None,
         "chapter_titles": ["X"]}))
    assert library.cache_status(db, h, None, "m4b") == "cleaned"
    # Display title is the filename stem (not the embedded meta title), and
    # est_duration uses the cached chunk char count.
    book = next(b for b in library.list_books(db) if b["book_id"] == h)
    assert book["title"] == "x" and book["est_duration_s"] is not None
