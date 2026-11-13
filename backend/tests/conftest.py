"""Shared fixtures. Each test gets an isolated data dir + library folder so the
suite never touches the real ~/Library state or the PDF library.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    data.mkdir()
    lib.mkdir()
    monkeypatch.setenv("PBS_DATA_DIR", str(data))
    monkeypatch.setenv("PBS_LIBRARY_DIR", str(lib))
    return {"data": data, "lib": lib}


@pytest.fixture
def db(env):
    from app.db import DB
    return DB()


def make_pdf(path: Path, title="Test Book", author="Tester",
             body="Hello world. This is a test paragraph for chunking."):
    """Create a tiny real PDF with extractable text (needs PyMuPDF)."""
    import pymupdf as fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), title, fontsize=18)
    page.insert_textbox(fitz.Rect(72, 120, 520, 700), body, fontsize=12)
    doc.set_metadata({"title": title, "author": author})
    doc.save(str(path))
    doc.close()
