"""Pre-clean cache: compute once, then load from disk on the next call."""
from .conftest import make_pdf


def test_prepare_or_load_computes_then_caches(env, monkeypatch):
    from app import config, library, pdfproc
    pdf = env["lib"] / "book.pdf"
    make_pdf(pdf, title="Cached Book", author="A",
             body="First sentence here. Second sentence follows. Third one too.")
    h = library.file_hash(pdf)

    first = pdfproc.prepare_or_load(str(pdf), h)
    assert first["title"] == "Cached Book"
    assert len(first["chunks"]) >= 1
    assert (config.preclean_dir() / h / "chunks.json").exists()
    assert (config.preclean_dir() / h / "meta.json").exists()

    # Second call must NOT recompute — guard by making prepare_job explode.
    def boom(*a, **k):
        raise AssertionError("prepare_job should not run on a cache hit")
    monkeypatch.setattr(pdfproc, "prepare_job", boom)

    second = pdfproc.prepare_or_load(str(pdf), h)
    assert second["title"] == first["title"]
    assert second["chunks"] == first["chunks"]
    assert second["chapter_titles"] == first["chapter_titles"]


def test_prepare_or_load_rebuilds_old_extraction_version(env, monkeypatch):
    import json
    from app import config, library, pdfproc

    pdf = env["lib"] / "versioned.pdf"
    make_pdf(pdf, title="Versioned Book", author="A")
    h = library.file_hash(pdf)
    pdfproc.prepare_or_load(str(pdf), h)

    meta_path = config.preclean_dir() / h / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["extraction_version"] = pdfproc.EXTRACTION_VERSION - 1
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    original = pdfproc.prepare_job
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(pdfproc, "prepare_job", counted)
    rebuilt = pdfproc.prepare_or_load(str(pdf), h)
    assert calls == 1
    assert rebuilt["extraction_version"] == pdfproc.EXTRACTION_VERSION
