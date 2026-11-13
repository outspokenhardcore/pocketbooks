"""API surface: auth, library, progress, and convert validation."""
import pytest
from fastapi.testclient import TestClient

from .conftest import make_pdf


@pytest.fixture
def client(db, env):
    from app import config
    from app.server import create_app
    from app.supervisor import Supervisor
    app = create_app(db, Supervisor(db))  # supervisor thread not started
    c = TestClient(app)
    token = config.get_or_create_token()
    c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def test_health_is_open(client):
    r = TestClient(client.app).get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_auth_required(client):
    unauth = TestClient(client.app)
    assert unauth.get("/api/library").status_code == 401
    assert unauth.put("/api/library/x/progress", json={"position_s": 1}).status_code == 401


def test_library_lists_pdfs(client, env):
    make_pdf(env["lib"] / "one.pdf", title="One")
    r = client.get("/api/library")
    assert r.status_code == 200
    books = r.json()["books"]
    # The cheap listing reads only the pre-clean cache, so before prepare the
    # title falls back to the filename stem.
    assert [b["filename"] for b in books] == ["one.pdf"]
    assert books[0]["title"] == "one"
    assert books[0]["cache_status"] == "none"


def test_progress_endpoints(client):
    put = client.put("/api/library/bookZ/progress",
                     json={"position_s": 30.0, "duration_s": 60.0, "device_id": "iphone"})
    assert put.status_code == 200 and put.json()["position_s"] == 30.0
    got = client.get("/api/library/bookZ/progress")
    assert got.status_code == 200 and got.json()["position_s"] == 30.0


def test_convert_unknown_book_404(client):
    r = client.post("/api/convert", json={"book_id": "missing", "voice_id": 1})
    assert r.status_code == 404


def test_convert_unknown_voice_400(client, env):
    make_pdf(env["lib"] / "b.pdf", title="B")
    from app import library
    book_id = library.file_hash(env["lib"] / "b.pdf")
    r = client.post("/api/convert", json={"book_id": book_id, "voice_id": 999})
    assert r.status_code == 400


def test_status_unknown_job_404(client):
    assert client.get("/api/jobs/123456/status").status_code == 404


def test_monitoring_endpoint_reports_idle_and_queue(client, db):
    idle = client.get("/api/monitoring")
    assert idle.status_code == 200
    assert idle.json()["state"] == "idle"
    assert idle.json()["queued_jobs"] == 0

    db.add_job("/tmp/book.pdf", 1, "/tmp")
    queued = client.get("/api/monitoring").json()
    assert queued["state"] == "queued"
    assert queued["queued_jobs"] == 1
    assert queued["progress_pct"] == 0


def test_convert_invalidates_audio_from_old_extraction_version(client, db, env):
    from app import config, library, pdfproc

    pdf = env["lib"] / "old-plan.pdf"
    make_pdf(pdf, title="Old Plan")
    book_id = library.file_hash(pdf)
    voice_id = db.add_voice("V", "/tmp/ref.wav", "hello", "/tmp/src.wav", 24000, 5)
    old_job = db.add_job(str(pdf), voice_id, str(config.audio_cache_dir()))
    db.update_job(old_job, pdf_hash=book_id, status="done", extraction_version=1)
    cache_dir = config.audio_cache_dir() / "old"
    cache_dir.mkdir(parents=True)
    (cache_dir / "stale.m4b").write_bytes(b"old")
    ckey = f"{book_id}:{voice_id}:m4b"
    db.upsert_cache(ckey, pdf_hash=book_id, voice_id=voice_id, format="m4b",
                    dir=str(cache_dir), job_id=old_job, state="ready")

    response = client.post("/api/convert", json={"book_id": book_id,
                                                  "voice_id": voice_id})
    assert response.status_code == 200
    # SQLite may reuse the integer rowid after deleting the only job; verify
    # state rather than relying on a numerically different id.
    replacement = db.job(response.json()["job_id"])
    assert replacement["status"] == "queued"
    assert replacement["total_chunks"] == 0
    assert db.get_cache(ckey) is None
    assert not cache_dir.exists()
    assert pdfproc.EXTRACTION_VERSION > 1


# ---- demo mode: no endpoint may spend money for a stranger -------------------

def test_library_advertises_recap_when_enabled(client):
    body = client.get("/api/library").json()
    assert body["features"]["recap"] is True
    assert body["demo"] is False


def test_demo_mode_disables_recap(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "RECAP_ENABLED", False)
    monkeypatch.setattr(config, "DEMO_MODE", True)

    body = client.get("/api/library").json()
    assert body["features"]["recap"] is False
    assert body["demo"] is True

    for path in ("/api/jobs/1/recap", "/api/library/deadbeef/recap"):
        r = client.post(path, json={"position_s": 10.0})
        assert r.status_code == 403, path
        assert "demo" in r.json()["detail"]


def test_demo_mode_leaves_listening_intact(client, monkeypatch):
    """Browsing and playback must keep working on a demo server."""
    from app import config
    monkeypatch.setattr(config, "RECAP_ENABLED", False)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    assert client.get("/api/library").status_code == 200
    assert client.get("/api/monitoring").status_code == 200


def test_recap_flag_defaults_follow_demo_mode(monkeypatch):
    import importlib
    from app import config

    monkeypatch.setenv("PBS_DEMO", "1")
    monkeypatch.delenv("PBS_RECAP", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.DEMO_MODE is True
    assert reloaded.RECAP_ENABLED is False

    # An explicit override still wins over the demo default.
    monkeypatch.setenv("PBS_RECAP", "1")
    reloaded = importlib.reload(config)
    assert reloaded.RECAP_ENABLED is True

    monkeypatch.delenv("PBS_DEMO", raising=False)
    monkeypatch.delenv("PBS_RECAP", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.DEMO_MODE is False
    assert reloaded.RECAP_ENABLED is True
