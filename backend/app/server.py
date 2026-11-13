"""Headless FastAPI backend for the iOS app.

Differences from the desktop edition:
  * No pywebview / file pickers — books come from a library folder (library.py).
  * Bearer-token auth on every /api route (expose only over Tailscale/WireGuard).
  * On-demand conversion with progressive HLS streaming (hls.py) so playback can
    start within seconds, while the book is still generating.
  * Cross-device listening position (progress) + small LRU audio cache.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import config, library
from .db import DB
from .recap import RecapManager
from .supervisor import Supervisor, cache_key


class ConvertReq(BaseModel):
    book_id: str
    voice_id: int
    output_format: str = "m4b"   # m4b (chapters) | mp3
    language: str = "auto"
    skip_front_matter: bool = True


class RecapReq(BaseModel):
    position_s: float


class ProgressReq(BaseModel):
    position_s: float
    duration_s: Optional[float] = None
    device_id: Optional[str] = None
    updated_at: Optional[float] = None  # client epoch; defaults to server now
    title: Optional[str] = None         # stored as fallback metadata
    author: Optional[str] = None


_SEG_MEDIA = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".mp4": "audio/mp4",
    ".m4s": "audio/mp4",
}


def create_app(db: DB, supervisor: Supervisor) -> FastAPI:
    app = FastAPI(title="PocketBooks", docs_url=None, redoc_url=None)
    token = config.get_or_create_token()
    recaps = RecapManager(db)

    # ---- auth ------------------------------------------------------------
    @app.middleware("http")
    async def require_token(request: Request, call_next):
        if request.url.path == "/health" or request.method == "OPTIONS":
            return await call_next(request)
        if request.headers.get("authorization", "") != f"Bearer {token}":
            return Response(status_code=401, content="unauthorized")
        return await call_next(request)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/monitoring")
    def monitoring():
        """Compact authenticated status for local monitoring/Home Assistant."""
        supervisor_state = supervisor.status_snapshot()
        current_id = supervisor_state.get("current_job_id")
        current = db.job(current_id) if current_id else None
        counts = {
            row["status"]: int(row["n"])
            for row in db.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        }
        queued = counts.get("queued", 0)
        if current:
            state = "processing"
        elif queued:
            state = "queued"
        elif supervisor_state.get("pausing") or counts.get("paused", 0):
            state = "paused"
        else:
            state = "idle"

        done = int(current.get("done_chunks") or 0) if current else 0
        total = int(current.get("total_chunks") or 0) if current else 0
        cache = db.query_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes),0) AS bytes "
            "FROM audio_cache WHERE state='ready'") or {"n": 0, "bytes": 0}
        return {
            "state": state,
            "current_job_id": current_id,
            "current_title": (current.get("title") or Path(current["pdf_path"]).stem)
                             if current else None,
            "done_chunks": done,
            "total_chunks": total,
            "progress_pct": round(done * 100 / total, 1) if total else 0,
            "queued_jobs": queued,
            "failed_jobs": counts.get("failed", 0),
            "ready_books": int(cache["n"] or 0),
            "cache_gb": round(int(cache["bytes"] or 0) / 1024 ** 3, 2),
            "eta_seconds": supervisor_state.get("eta_seconds"),
        }

    # ---- library ---------------------------------------------------------
    @app.get("/api/library")
    def get_library(folder: str = "", q: Optional[str] = None,
                    voice_id: Optional[int] = None, fmt: str = "m4b"):
        result = library.browse(db, folder=folder, q=q, voice_id=voice_id, fmt=fmt)
        result["voices"] = db.voices()
        # Lets the client hide controls the server will refuse anyway.
        result["features"] = {"recap": config.RECAP_ENABLED}
        result["demo"] = config.DEMO_MODE
        return result

    @app.get("/api/started")
    def started(voice_id: Optional[int] = None, fmt: str = "m4b"):
        """Books the user has begun listening to ("Continue listening" tab)."""
        return {"books": library.started_books(db, voice_id, fmt)}

    @app.get("/api/library/{book_id}/cover")
    def book_cover(book_id: str):
        # Lazily extract on first request so covers appear while browsing,
        # without starting a conversion.
        cover = library.ensure_cover(book_id)
        if not cover:
            raise HTTPException(404, "no cover")
        return FileResponse(cover, media_type="image/jpeg")

    @app.post("/api/library/{book_id}/prepare")
    def prepare(book_id: str, skip_front_matter: bool = True):
        """Force pre-clean (metadata/cover/chunk plan) without converting."""
        pdf = library.resolve_pdf(book_id)
        if not pdf:
            raise HTTPException(404, "book not in library")
        from .pdfproc import prepare_or_load
        meta = prepare_or_load(str(pdf), book_id,
                               skip_front_matter=skip_front_matter)
        return {"title": meta["title"], "author": meta["author"],
                "chapters": meta.get("chapter_titles") or [],
                "chunks": len(meta["chunks"])}

    # ---- conversion / streaming -----------------------------------------
    @app.post("/api/convert")
    def convert(req: ConvertReq):
        from .pdfproc import EXTRACTION_VERSION

        pdf = library.resolve_pdf(req.book_id)
        if not pdf:
            raise HTTPException(404, "book not in library")
        if not db.voice(req.voice_id):
            raise HTTPException(400, "unknown voice")
        fmt = req.output_format
        if fmt not in ("m4b", "mp3"):
            raise HTTPException(400, "output_format must be m4b or mp3")

        ckey = cache_key(req.book_id, req.voice_id, fmt)
        cached = db.get_cache(ckey)
        existing = db.job_by_hash(req.book_id, req.voice_id, fmt)

        # A ready audio file was generated from a frozen chunk plan. If the
        # extraction algorithm changes, reusing it would preserve old headers
        # or front matter forever. Invalidate lazily when the book is next
        # opened; listening progress is keyed separately and remains intact.
        if existing and int(existing.get("extraction_version") or 1) < EXTRACTION_VERSION:
            if existing["status"] == "processing":
                supervisor.cancel_job(existing["id"])
            if cached and cached.get("dir"):
                shutil.rmtree(cached["dir"], ignore_errors=True)
                db.delete_cache(ckey)
            db.delete_job(existing["id"])
            existing = None
            cached = None

        if existing and existing["status"] in ("processing", "queued", "done"):
            job_id = existing["id"]
            db.update_job(job_id, pdf_path=str(pdf))   # file may have moved
        elif existing and existing["status"] in ("paused", "failed"):
            job_id = existing["id"]
            # Refresh the path in case the PDF was moved into a subfolder; the
            # content hash is unchanged so the checkpoint/cache still apply.
            db.update_job(job_id, pdf_path=str(pdf), error=None)
            supervisor.resume_job(job_id)
        else:
            job_id = db.add_job(str(pdf), req.voice_id,
                                str(config.audio_cache_dir()), req.language,
                                fmt, req.skip_front_matter)
            db.update_job(job_id, pdf_hash=req.book_id)
        if cached:
            db.touch_cache(ckey)
        supervisor.start_queue()
        supervisor.request_job(job_id)  # the just-opened book takes priority
        return {"job_id": job_id, "book_id": req.book_id,
                "stream_url": f"/api/stream/{job_id}/index.m3u8",
                "status": _job_status(db, job_id)}

    @app.get("/api/jobs/{job_id}/status")
    def job_status(job_id: int):
        if not db.job(job_id):
            raise HTTPException(404, "unknown job")
        return _job_status(db, job_id)

    @app.get("/api/jobs/{job_id}/mp3")
    async def stream_mp3(job_id: int):
        """Stream the growing MP3 file for a running job. Valid MP3 from the
        first chunk — clients can start downloading immediately while
        generation is still in progress. Connection stays open until the
        job completes or the client disconnects."""
        job = db.job(job_id)
        if not job:
            raise HTTPException(404, "unknown job")
        if not job.get("work_dir"):
            raise HTTPException(404, "job has no work directory yet")
        mp3_path = Path(job["work_dir"]).parent / "download.mp3"
        if not mp3_path.exists():
            raise HTTPException(404, "mp3 not available (job may not have started yet)")

        async def file_iterator():
            with open(mp3_path, "rb") as f:
                while True:
                    data = f.read(65536)
                    if not data:
                        current = db.job(job_id)
                        if current and current["status"] == "processing":
                            await asyncio.sleep(0.5)
                            continue
                        break
                    yield data

        filename = (job.get("title") or "audiobook") + ".mp3"
        # Sanitize filename
        filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
        return StreamingResponse(
            file_iterator(), media_type="audio/mpeg",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        """Stop a queued or running job. Idempotent: cancelling an already-
        cancelled or done job is a no-op. The job's audio_cache row is removed
        so the Library view stops reporting cacheStatus='partial' for it."""
        job = db.job(job_id)
        if not job:
            raise HTTPException(404, "unknown job")
        supervisor.cancel_job(job_id)
        # Drop the (stale) audio_cache row so cacheStatus flips back to 'cleaned'.
        if (job.get("pdf_hash") and job.get("voice_id") is not None
                and job.get("output_format")):
            try:
                from .supervisor import cache_key as _cache_key
                ckey = _cache_key(job["pdf_hash"], job["voice_id"],
                                  job["output_format"])
                db.delete_cache(ckey)
            except Exception:
                pass  # best-effort
        return {"job_id": job_id, "status": db.job(job_id)["status"]}

    @app.get("/api/voices")
    def voices_endpoint():
        return [{"id": v["id"], "name": v["name"]} for v in db.voices()]

    @app.get("/api/stream/{job_id}/{filename}")
    def stream_file(job_id: int, filename: str):
        job = db.job(job_id)
        if not job or not job.get("stream_dir"):
            raise HTTPException(404, "no stream for this job")
        # Defend against path traversal: only plain segment/playlist names.
        if "/" in filename or "\\" in filename or filename.startswith("."):
            raise HTTPException(400, "bad filename")
        path = Path(job["stream_dir"]) / filename
        if not path.is_file():
            raise HTTPException(404, "segment not ready")
        if filename.endswith(".m3u8") and job.get("pdf_hash") is not None:
            db.touch_cache(cache_key(job["pdf_hash"], job["voice_id"],
                                     job.get("output_format") or "m4b"))
        media = _SEG_MEDIA.get(path.suffix, "application/octet-stream")
        return FileResponse(path, media_type=media)

    @app.get("/api/jobs/{job_id}/chapters")
    def chapters(job_id: int):
        return {"chapters": _chapters(db, job_id)}

    @app.get("/api/jobs/{job_id}/transcript")
    def transcript(job_id: int):
        """Time-synced text ("lyrics") for the generated audio so far."""
        return {"lines": _transcript(db, job_id)}

    # ---- "previously on…" recap -----------------------------------------
    def _require_recap() -> None:
        """Recap calls a paid third-party API, so a demo server turns it off."""
        if not config.RECAP_ENABLED:
            raise HTTPException(
                403, "recap is disabled on this server (demo mode)")

    @app.post("/api/jobs/{job_id}/recap")
    def make_recap(job_id: int, req: RecapReq):
        _require_recap()
        if not db.job(job_id):
            raise HTTPException(404, "unknown job")
        try:
            rid = recaps.request(job_id, req.position_s)
        except KeyError:
            raise HTTPException(404, "unknown job")
        return {"recap_id": rid, **(recaps.get(rid) or {})}

    @app.post("/api/library/{book_id}/recap")
    def make_recap_book(book_id: str, req: RecapReq):
        """Recap by book — works for offline-played downloads too (resolves the
        book's most recent generated job server-side)."""
        _require_recap()
        job = db.latest_job_for_book(book_id)
        if not job:
            raise HTTPException(404, "no generated audio for this book yet")
        rid = recaps.request(job["id"], req.position_s)
        return {"recap_id": rid, **(recaps.get(rid) or {})}

    @app.get("/api/recap/{recap_id}")
    def recap_status(recap_id: str):
        r = recaps.get(recap_id)
        if not r:
            raise HTTPException(404, "unknown recap")
        return {"state": r["state"], "text": r["text"],
                "has_audio": bool(r.get("audio")), "error": r.get("error"),
                "audio_url": f"/api/recap/{recap_id}/audio" if r.get("audio") else None}

    @app.get("/api/recap/{recap_id}/audio")
    def recap_audio(recap_id: str):
        r = recaps.get(recap_id)
        if not r or not r.get("audio") or not Path(r["audio"]).exists():
            raise HTTPException(404, "no recap audio")
        return FileResponse(r["audio"], media_type="audio/mp4")

    @app.get("/api/jobs/{job_id}/audio")
    def job_audio(job_id: int):
        j = db.job(job_id)
        path = (j or {}).get("output_mp3_path")
        if not path or not Path(path).exists():
            raise HTTPException(404, "no finished audio for this job")
        mt = "audio/mp4" if path.lower().endswith((".m4b", ".m4a")) \
            else "audio/mpeg"
        return FileResponse(path, media_type=mt, filename=Path(path).name)

    # ---- listening position (cross-device) ------------------------------
    @app.get("/api/library/{book_id}/progress")
    def get_progress(book_id: str):
        return db.get_progress(book_id) or {
            "book_id": book_id, "position_s": 0.0,
            "duration_s": None, "updated_at": 0}

    @app.put("/api/library/{book_id}/progress")
    def put_progress(book_id: str, req: ProgressReq):
        # Enrich title/author from the most recent job when the client omits them,
        # so ghost entries in started_books() always have human-readable metadata.
        title, author = req.title, req.author
        if not title or not author:
            job = db.latest_job_for_book(book_id)
            if job:
                title = title or job.get("title")
                author = author or job.get("author")
        return db.set_progress(book_id, req.position_s, req.duration_s,
                               req.device_id, req.updated_at or time.time(),
                               title, author)

    @app.delete("/api/library/{book_id}/progress")
    def delete_progress(book_id: str):
        """Forget the listening position (removes it from "Continue listening")."""
        db.delete_progress(book_id)
        return {"ok": True}

    return app


# --- helpers -----------------------------------------------------------------

def _job_status(db: DB, job_id: int) -> dict:
    j = db.job(job_id)
    if not j:
        return {"state": "unknown"}
    done = db.query(
        "SELECT COALESCE(SUM(duration),0) AS s FROM chunks "
        "WHERE job_id=? AND status='done'", (job_id,))
    available_s = float(done[0]["s"]) if done else 0.0
    ready = False
    sd = j.get("stream_dir")
    if sd:
        try:
            ready = ".m4s" in (Path(sd) / "index.m3u8").read_text(encoding="utf-8")
        except OSError:
            ready = False
    return {
        "job_id": job_id,
        "book_id": j.get("pdf_hash"),
        "state": j["status"],
        "done_chunks": j["done_chunks"],
        "total_chunks": j["total_chunks"],
        "failed_chunks": j["failed_chunks"],
        "available_seconds": available_s,
        "playable": ready,
        "stream_url": f"/api/stream/{job_id}/index.m3u8",
        "has_m4b": bool(j.get("output_mp3_path")),
        "title": j.get("title"),
        "author": j.get("author"),
    }


def _chapters(db: DB, job_id: int) -> list[dict]:
    """Chapter start times from the chunk plan, using known durations + the same
    inter-chunk gaps as the M4B. Chapters whose first chunk isn't generated yet
    get start_s=None so the app can show them greyed-out."""
    import json
    j = db.job(job_id)
    if not j:
        return []
    titles = []
    if j.get("chapters_json"):
        try:
            titles = json.loads(j["chapters_json"])
        except (json.JSONDecodeError, TypeError):
            titles = []
    rows = db.query("SELECT * FROM chunks WHERE job_id=? ORDER BY idx", (job_id,))
    out: list[dict] = []
    t = 0.0
    seen: set[int] = set()
    for i, ch in enumerate(rows):
        ci = int(ch["chapter"] or 0)
        if ci not in seen:
            seen.add(ci)
            title = titles[ci] if ci < len(titles) else f"Chapter {ci + 1}"
            out.append({"index": ci, "title": title,
                        "start_s": t if ch["status"] == "done" else None})
        if ch["status"] == "done":
            t += float(ch["duration"] or 0)
            if i < len(rows) - 1:
                t += (config.GAP_PARAGRAPH_S if ch["para_end"]
                      else config.GAP_SENTENCE_S)
    return out


_SENT_SPLIT = __import__("re").compile(r"(?<=[.!?…])\s+")


def _transcript(db: DB, job_id: int) -> list[dict]:
    """Sentence-level timed lines for the audio generated so far.

    Mirrors the HLS/chapter timeline: only done chunks advance the clock (in
    order, gaps included). Each chunk's duration is apportioned across its
    sentences by character length — approximate but visually accurate enough
    for an auto-scrolling running-text view. Pocket TTS gives no word timings,
    so finer than this would need forced alignment."""
    done = db.query(
        "SELECT * FROM chunks WHERE job_id=? AND status='done' ORDER BY idx",
        (job_id,))
    lines: list[dict] = []
    t = 0.0
    for i, ch in enumerate(done):
        dur = float(ch["duration"] or 0)
        ci = int(ch["chapter"] or 0)
        sents = [s for s in _SENT_SPLIT.split((ch["text"] or "").strip()) if s]
        total = sum(len(s) for s in sents) or 1
        st = t
        for s in sents:
            seg = dur * len(s) / total
            lines.append({"start_s": round(st, 3), "end_s": round(st + seg, 3),
                          "text": s, "chapter": ci})
            st += seg
        t += dur
        if i < len(done) - 1:
            t += (config.GAP_PARAGRAPH_S if ch["para_end"]
                  else config.GAP_SENTENCE_S)
    return lines
