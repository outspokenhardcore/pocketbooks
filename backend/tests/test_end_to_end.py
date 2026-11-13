"""Exercise the PDF -> API -> worker -> FFmpeg -> download path with fake TTS."""
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_pdf


@pytest.mark.parametrize(
    ("output_format", "download_path", "content_type"),
    [("m4b", "audio", "audio/mp4"), ("mp3", "mp3", "audio/mpeg")],
)
def test_pdf_to_download(db, env, monkeypatch,
                         output_format, download_path, content_type):
    """Use the real queue worker and ffmpeg muxers; replace only TTS inference."""
    from app import config, supervisor as supervisor_module
    from app.server import create_app
    from app.supervisor import Supervisor

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for the end-to-end audio test")
    monkeypatch.setenv("PBS_FFMPEG", ffmpeg)
    monkeypatch.setattr(supervisor_module, "ENGINE_KIND", "fake")

    pdf_path = env["lib"] / "synthetic-book.pdf"
    make_pdf(pdf_path, title="E2E Test Book", body=(
        "This synthetic passage exercises the complete audiobook pipeline. "
        "The test covers extraction, queue processing, audio encoding, and download."
    ))

    supervisor = Supervisor(db)
    supervisor.start()
    supervisor.start_queue()
    client = TestClient(create_app(db, supervisor))
    client.headers["Authorization"] = f"Bearer {config.get_or_create_token()}"
    try:
        listed = client.get("/api/library")
        assert listed.status_code == 200
        book = listed.json()["books"][0]
        response = client.post("/api/convert", json={
            "book_id": book["book_id"],
            "voice_id": 1,
            "output_format": output_format,
            "skip_front_matter": False,
        })
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]

        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = db.job(job_id)
            if job["status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert db.job(job_id)["status"] == "done", db.job(job_id)

        download = client.get(f"/api/jobs/{job_id}/{download_path}")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith(content_type)
        assert len(download.content) > 1000
        if output_format == "mp3":
            assert download.content[:3] == b"ID3"
        else:
            assert download.content[4:8] == b"ftyp"
    finally:
        supervisor.shutdown()
        supervisor._thread.join(timeout=5)
