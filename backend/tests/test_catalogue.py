"""Static catalogue export: the demo server with no Python in it.

Builds a tiny real library (PDF + job + chunks + HLS files) and exports it,
then checks the tree is complete and self-consistent.
"""
import json

import pytest

from .conftest import make_pdf

PLAYLIST = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:10
#EXT-X-MAP:URI="init.mp4"
#EXTINF:10.0,
seg_00000.m4s
#EXTINF:8.0,
seg_00001.m4s
#EXT-X-ENDLIST
"""


def _ready_book(env, db, *, with_endlist=True, with_m4b=True):
    """A library PDF with a finished job and its audio cache on disk."""
    from app import config, library

    pdf = env["lib"] / "Demo Book.pdf"
    make_pdf(pdf, title="Demo Book", author="A. Writer",
             body="First sentence. Second sentence. Third one as well.")
    book_id = library.file_hash(pdf)

    job_id = db.add_job(str(pdf), 1, str(env["data"]))
    db.write_chunk_plan(job_id, [("First sentence.", 0, 0),
                                 ("Second sentence.", 0, 1)])
    for idx in (0, 1):
        db.update_chunk(job_id, idx, status="done", duration=9.0)
    db.update_job(job_id, pdf_hash=book_id, status="done", output_format="m4b",
                  title="Demo Book", author="A. Writer",
                  chapters_json=json.dumps(["Chapter I", "Chapter II"]))

    cache = config.audio_cache_dir() / f"{book_id}_1_m4b"
    hls = cache / "hls"
    hls.mkdir(parents=True, exist_ok=True)
    db.update_job(job_id, stream_dir=str(hls))
    text = PLAYLIST if with_endlist else PLAYLIST.replace("#EXT-X-ENDLIST\n", "")
    (hls / "index.m3u8").write_text(text, encoding="utf-8")
    (hls / "init.mp4").write_bytes(b"init-segment")
    (hls / "seg_00000.m4s").write_bytes(b"segment-zero")
    (hls / "seg_00001.m4s").write_bytes(b"segment-one")

    if with_m4b:
        out = cache / "Demo Book.m4b"
        out.write_bytes(b"m4b-bytes")
        db.update_job(job_id, output_mp3_path=str(out))

    return book_id, job_id


def test_export_writes_a_complete_tree(env, db, tmp_path):
    from app import catalogue

    book_id, _ = _ready_book(env, db)
    result = catalogue.export(tmp_path, voice_id=1, with_download=True, db=db)

    assert len(result.books) == 1
    book = result.books[0]
    assert book.book_id == book_id
    assert book.title == "Demo Book"
    assert book.author == "A. Writer"
    assert book.chapters == 2
    assert book.duration_s == pytest.approx(18.0)
    assert book.bytes > 0

    for rel in (book.chapters_url, book.transcript_url, book.hls_url,
                book.m4b_url, book.cover_url):
        assert rel, "every url should be present for a fully generated book"
        assert (tmp_path / rel).exists(), rel

    assert catalogue.check(tmp_path) == []


def test_the_m4b_is_left_out_unless_asked_for(env, db, tmp_path):
    """It roughly doubles the catalogue and the app cannot use it anyway."""
    from app import catalogue

    _ready_book(env, db)
    lean = catalogue.export(tmp_path / "lean", voice_id=1, db=db)
    full = catalogue.export(tmp_path / "full", voice_id=1, with_download=True, db=db)

    assert lean.books[0].m4b_url is None
    assert full.books[0].m4b_url is not None
    assert lean.books[0].bytes < full.books[0].bytes
    assert catalogue.check(tmp_path / "lean") == []


def test_manifest_is_readable_without_the_server(tmp_path, env, db):
    from app import catalogue

    _ready_book(env, db)
    catalogue.export(tmp_path, voice_id=1, db=db)
    data = json.loads((tmp_path / "catalogue.json").read_text(encoding="utf-8"))

    assert data["version"] == catalogue.CATALOGUE_VERSION
    assert data["voice_id"] == 1
    assert [b["title"] for b in data["books"]] == ["Demo Book"]
    # Relative URLs only — the catalogue must work under any subpath.
    for book in data["books"]:
        for key in ("cover_url", "chapters_url", "transcript_url", "hls_url"):
            assert not book[key].startswith(("/", "http")), book[key]


def test_exported_json_matches_the_live_api_shape(tmp_path, env, db):
    """The app decodes both with the same models, so they must not drift."""
    from app import catalogue
    from app.server import _chapters, _transcript

    _, job_id = _ready_book(env, db)
    catalogue.export(tmp_path, voice_id=1, db=db)
    book = json.loads((tmp_path / "catalogue.json").read_text())["books"][0]

    chapters = json.loads((tmp_path / book["chapters_url"]).read_text())
    transcript = json.loads((tmp_path / book["transcript_url"]).read_text())
    assert chapters == {"chapters": _chapters(db, job_id)}
    assert transcript == {"lines": _transcript(db, job_id)}
    assert set(chapters["chapters"][0]) == {"index", "title", "start_s"}
    assert set(transcript["lines"][0]) == {"start_s", "end_s", "text", "chapter"}


def test_unfinished_playlist_is_skipped(env, db, tmp_path):
    """An EVENT playlist without ENDLIST would make the player wait forever."""
    from app import catalogue

    _ready_book(env, db, with_endlist=False)
    result = catalogue.export(tmp_path, voice_id=1, db=db)
    assert result.books == []
    assert any("ENDLIST" in note for note in catalogue.export.skipped)


def test_books_without_generated_audio_are_reported_not_exported(env, db, tmp_path):
    from app import catalogue

    make_pdf(env["lib"] / "Never Converted.pdf", title="Never Converted")
    result = catalogue.export(tmp_path, voice_id=1, db=db)
    assert result.books == []
    assert any("not generated" in note for note in catalogue.export.skipped)


def test_check_detects_a_missing_segment(env, db, tmp_path):
    from app import catalogue

    book_id, _ = _ready_book(env, db)
    catalogue.export(tmp_path, voice_id=1, db=db)
    (tmp_path / "books" / book_id / "hls" / "seg_00001.m4s").unlink()

    problems = catalogue.check(tmp_path)
    assert any("seg_00001.m4s" in p for p in problems)


def test_check_detects_a_truncated_file(env, db, tmp_path):
    from app import catalogue

    book_id, _ = _ready_book(env, db)
    catalogue.export(tmp_path, voice_id=1, db=db)
    (tmp_path / "books" / book_id / "chapters.json").write_bytes(b"")

    assert any("empty" in p for p in catalogue.check(tmp_path))


def test_check_reports_a_missing_manifest(tmp_path):
    from app import catalogue
    assert catalogue.check(tmp_path) == ["catalogue.json is missing"]


def test_cli_export_then_check(env, db, tmp_path, capsys):
    from app import catalogue

    _ready_book(env, db)
    assert catalogue.main(["export", "--dest", str(tmp_path), "--voice", "1"]) == 0
    assert "Demo Book" in capsys.readouterr().out
    assert catalogue.main(["check", "--dest", str(tmp_path)]) == 0
    assert "complete" in capsys.readouterr().out


def test_cli_export_fails_loudly_on_an_empty_library(env, tmp_path, capsys):
    from app import catalogue
    assert catalogue.main(["export", "--dest", str(tmp_path)]) == 1
    assert "generate the books first" in capsys.readouterr().err
