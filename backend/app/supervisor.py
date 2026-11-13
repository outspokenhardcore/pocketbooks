"""Queue supervisor.

Runs in the app process as a daemon thread. For each job it:
  1. builds (or reuses) the frozen chunk plan in SQLite,
  2. pre-flights the model download (separate killable process, own timeout),
  3. spawns ONE worker child process (model loaded once, reused per chunk),
  4. dispatches pending chunks one by one under an adaptive WATCHDOG timeout —
     a hung chunk gets the worker SIGKILLed, restarted, and the chunk retried;
     after CHUNK_MAX_ATTEMPTS the chunk is marked failed and the job continues,
  5. checkpoints every finished chunk (wav on disk + SQLite row) so restart
     resumes from the next chunk,
  6. assembles the final MP3 with cover + ID3 tags.

A `caffeinate` child prevents macOS sleep while the queue is active.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import config
from .db import DB
from .hls import StreamAssembler, MP3Streamer
ENGINE_KIND = os.environ.get("PBS_ENGINE", "pocket")


def cache_key(pdf_hash: str, voice_id, fmt: str) -> str:
    return f"{pdf_hash}:{voice_id}:{fmt}"


def cache_dir_for(pdf_hash: str, voice_id, fmt: str) -> Path:
    safe = cache_key(pdf_hash, voice_id, fmt).replace(":", "_")
    return config.audio_cache_dir() / safe


class WorkerHandle:
    """One TTS worker child process + a reader thread feeding an event queue."""

    def __init__(self, job: dict, voice: dict, work_dir: Path, log_file):
        ref_text_file = work_dir / "ref_text.txt"
        ref_text_file.write_text(voice.get("ref_text") or "", encoding="utf-8")
        cmd = [
            sys.executable, "-u", "-m", "app.worker",
            "--engine", ENGINE_KIND,
            "--ref-audio", voice["ref_wav_path"],
            "--ref-text-file", str(ref_text_file),
            "--language", job.get("language") or "auto",
            "--out-dir", str(work_dir / "chunks"),
        ]
        env = config.hf_env()
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=log_file, text=True, bufsize=1, env=env,
            cwd=str(Path(__file__).resolve().parent.parent),
            start_new_session=True,
        )
        self.events: "queue.Queue[dict]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        try:
            for line in self.proc.stdout:
                if line.startswith("@@"):
                    try:
                        self.events.put(json.loads(line[2:]))
                    except json.JSONDecodeError:
                        pass
        except ValueError:
            pass
        self.events.put({"event": "_eof"})

    def send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def wait_event(self, timeout: float) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                ev = self.events.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            return ev

    def _close_pipes(self) -> None:
        for pipe in (self.proc.stdin, self.proc.stdout):
            try:
                if pipe:
                    pipe.close()
            except Exception:
                pass

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self._close_pipes()

    def quit_gracefully(self) -> None:
        try:
            self.send({"cmd": "quit"})
            self.proc.wait(timeout=5)
            self._close_pipes()
        except Exception:
            self.kill()


class Supervisor:
    def __init__(self, db: DB):
        self.db = db
        self._wake = threading.Event()
        self._queue_active = threading.Event()
        self._pause_requested = threading.Event()
        self._preempt = threading.Event()
        self._preferred_job_id: Optional[int] = None
        self._shutdown = threading.Event()
        self._current_job_id: Optional[int] = None
        self._current_chunk: Optional[dict] = None
        self._chunk_started_at: float = 0.0
        self._rate_chars_per_s: float = 0.0
        self._caffeinate: Optional[subprocess.Popen] = None
        self.download_status: dict = {}
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.db.recover_interrupted()
        if self.db.get_setting("queue_active") == "1":
            self._queue_active.set()
        self._thread.start()

    def start_queue(self) -> None:
        self._pause_requested.clear()
        self.db.execute("UPDATE jobs SET status='queued' WHERE status='paused'")
        self._queue_active.set()
        self.db.set_setting("queue_active", "1")
        self._wake.set()

    def pause_queue(self) -> None:
        self._pause_requested.set()
        self._queue_active.clear()
        self.db.set_setting("queue_active", "0")

    def pause_job(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job:
            return
        if job["status"] == "queued":
            self.db.update_job(job_id, status="paused")
        elif job["status"] == "processing":
            self._pause_requested.set()

    def resume_job(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if job and job["status"] in ("paused", "failed"):
            self.db.update_job(job_id, status="queued", error=None)
            self._wake.set()

    def cancel_job(self, job_id: int) -> None:
        job = self.db.job(job_id)
        if not job:
            return
        if job["status"] == "processing":
            self._pause_requested.set()
        self.db.update_job(job_id, status="cancelled")

    def wake(self) -> None:
        """Nudge the run loop to pick up a newly queued on-demand job."""
        self._wake.set()

    def request_job(self, job_id: int) -> None:
        """The book the user just opened takes priority. The current job keeps
        running until it finishes its chunk; only then the new job starts.
        This is the single-slot queue: at most one generation runs at a time,
        and job_ids are processed FIFO with `requested` jobs getting priority.
        """
        self._preferred_job_id = job_id
        if self._current_job_id is not None and self._current_job_id != job_id:
            # _run_job checks this between chunks, so the current chunk can
            # finish and checkpoint before the preferred book takes over.
            self._preempt.set()
        self._wake.set()

    def shutdown(self) -> None:
        self._shutdown.set()
        self._pause_requested.set()
        self._wake.set()

    def status_snapshot(self) -> dict:
        eta = None
        cur = self._current_chunk
        if cur is not None and self._rate_chars_per_s > 0 and self._current_job_id:
            pend = self.db.query_one(
                "SELECT COALESCE(SUM(LENGTH(text)),0) AS c FROM chunks "
                "WHERE job_id=? AND status='pending'", (self._current_job_id,))
            eta = (pend["c"] if pend else 0) / self._rate_chars_per_s
        return {
            "queue_active": self._queue_active.is_set(),
            "pausing": (self._pause_requested.is_set()
                        and self._current_job_id is not None),
            "current_job_id": self._current_job_id,
            "current_chunk_idx": cur["idx"] if cur else None,
            "current_chunk_text": (cur["text"][:200] if cur else None),
            "chunk_elapsed": time.time() - self._chunk_started_at if cur else None,
            "eta_seconds": eta,
            "download": self.download_status or None,
        }

    def _pick_job(self) -> Optional[dict]:
        """Preferred (just-opened) book first, otherwise FIFO by id."""
        if not self._queue_active.is_set():
            return None
        pid = self._preferred_job_id
        if pid is not None:
            j = self.db.job(pid)
            if j and j["status"] == "queued":
                return j
        return self.db.next_queued_job()

    def _run(self) -> None:
        while not self._shutdown.is_set():
            job = self._pick_job()
            if job is None:
                # Idle: keep the Mac awake & reachable during the day, but allow
                # it to sleep inside the nightly window (nobody is listening).
                if config.in_sleep_window():
                    self._stop_caffeinate()
                else:
                    self._start_caffeinate()
                self._wake.wait(timeout=1.0)
                self._wake.clear()
                continue
            self._start_caffeinate()
            self._preempt.clear()  # fresh start; only later requests preempt
            try:
                self._process_job(job)
            except Exception as exc:
                self.db.update_job(job["id"], status="failed", error=str(exc))
            finally:
                self._current_job_id = None
                self._current_chunk = None

    def _process_job(self, job: dict) -> None:
        job_id = job["id"]
        self._current_job_id = job_id
        log_path = config.logs_dir() / f"job_{job_id}.log"
        with open(log_path, "a", buffering=1) as log_file:
            self._process_job_logged(job, log_file)

    def _process_job_logged(self, job: dict, log_file) -> None:
        job_id = job["id"]
        log = lambda m: log_file.write(f"[{time.strftime('%F %T')}] {m}\n")
        log(f"=== processing job {job_id}: {job['pdf_path']}")

        voice = self.db.voice(job["voice_id"]) if job["voice_id"] else None
        if not voice:
            # When using piper (or any engine that ignores ref_audio), fall back
            # to the first available voice so old jobs still work.
            if ENGINE_KIND == "piper":
                all_voices = self.db.voices()
                voice = all_voices[0] if all_voices else None
                if voice:
                    self.db.update_job(job_id, voice_id=voice["id"])
            if not voice:
                self.db.update_job(job_id, status="failed", error="voice not found")
                return
        if not Path(job["pdf_path"]).exists():
            self.db.update_job(job_id, status="failed", error="PDF not found")
            return

        # Everything for this (book, voice, format) lives under one cache dir:
        # the chunk WAVs (work/), the growing HLS stream (hls/) and the final
        # M4B. The audio_cache row drives LRU eviction.
        fmt = job.get("output_format") or "m4b"
        pdf_hash = job.get("pdf_hash")
        if not pdf_hash:
            from .library import file_hash
            pdf_hash = file_hash(job["pdf_path"])
            self.db.update_job(job_id, pdf_hash=pdf_hash)
        ckey = cache_key(pdf_hash, job["voice_id"], fmt)
        cdir = cache_dir_for(pdf_hash, job["voice_id"], fmt)
        work_dir = cdir / "work"
        stream_dir = cdir / "hls"
        (work_dir / "chunks").mkdir(parents=True, exist_ok=True)
        self.db.upsert_cache(ckey, pdf_hash=pdf_hash, voice_id=job["voice_id"],
                             format=fmt, dir=str(cdir), job_id=job_id,
                             state="partial")
        self.db.update_job(job_id, status="processing", work_dir=str(work_dir),
                           stream_dir=str(stream_dir),
                           started_at=job["started_at"] or time.time())

        if job["total_chunks"] == 0:
            from .pdfproc import EXTRACTION_VERSION, prepare_or_load
            log("loading/building clean chunk plan (pre-clean cache)")
            meta = prepare_or_load(
                job["pdf_path"], pdf_hash,
                skip_front_matter=bool(job.get("skip_front_matter", 1)))
            self.db.write_chunk_plan(job_id, meta["chunks"])
            self.db.update_job(
                job_id, total_chunks=len(meta["chunks"]), title=meta["title"],
                author=meta["author"], track=meta["track"],
                series=meta.get("series"),
                cover_path=meta["cover_path"],
                extraction_version=EXTRACTION_VERSION,
                chapters_json=json.dumps(meta.get("chapter_titles") or []))
            log(f"chunk plan: {len(meta['chunks'])} chunks, "
                f"{len(meta.get('chapter_titles') or [])} chapters")

        # Rebuild the HLS stream from scratch each run: feed every already-done
        # chunk (in order) into a fresh ffmpeg muxer, then continue live. AAC
        # re-encode of done chunks is cheap and avoids cross-restart bookkeeping.
        assembler = StreamAssembler(stream_dir)
        for ch in self.db.query(
                "SELECT * FROM chunks WHERE job_id=? AND status='done' "
                "ORDER BY idx", (job_id,)):
            assembler.feed_wav(ch["wav_path"], bool(ch["para_end"]), is_last=False)

        # Also write chunks into a growing MP3 file for direct download.
        mp3_path = work_dir.parent / "download.mp3"
        mp3_streamer = MP3Streamer(mp3_path)
        for ch in self.db.query(
                "SELECT * FROM chunks WHERE job_id=? AND status='done' "
                "ORDER BY idx", (job_id,)):
            mp3_streamer.feed_wav(ch["wav_path"], bool(ch["para_end"]), is_last=False)

        worker: Optional[WorkerHandle] = None
        chunks_since_recycle = 0
        try:
            while not self._shutdown.is_set():
                if self._pause_requested.is_set():
                    self.db.update_job(job_id, status="paused")
                    log("paused by user")
                    return
                if self._preempt.is_set() and self._preferred_job_id != job_id:
                    self._preempt.clear()
                    self.db.update_job(job_id, status="queued")
                    log(f"preempted in favor of job {self._preferred_job_id} "
                        f"(the book just opened); will resume from checkpoint")
                    return
                job_now = self.db.job(job_id)
                if job_now is None or job_now["status"] == "cancelled":
                    log("cancelled")
                    return
                pending = self.db.pending_chunks(job_id)
                if not pending:
                    break
                chunk = pending[0]

                if worker is None or chunks_since_recycle >= config.WORKER_RECYCLE_EVERY_CHUNKS:
                    if worker:
                        log("recycling worker (memory hygiene)")
                        worker.quit_gracefully()
                    worker = self._spawn_ready_worker(job_now, voice, work_dir, log_file, log)
                    if worker is None:
                        self.db.update_job(job_id, status="failed",
                                           error="worker failed to initialize (see job log)")
                        return
                    chunks_since_recycle = 0

                self._current_chunk = chunk
                self._chunk_started_at = time.time()
                timeout = max(config.CHUNK_TIMEOUT_BASE_S,
                              len(chunk["text"]) * config.CHUNK_TIMEOUT_PER_CHAR_S)
                worker.send({"cmd": "chunk", "idx": chunk["idx"], "text": chunk["text"]})
                ev = worker.wait_event(timeout)

                if ev is None or ev.get("event") == "_eof":
                    reason = "watchdog timeout" if ev is None else "worker died"
                    log(f"chunk {chunk['idx']}: {reason} (attempt {chunk['attempts'] + 1})")
                    worker.kill()
                    worker = None
                    self._register_chunk_failure(job_id, chunk, reason, log)
                elif ev.get("event") == "chunk_done":
                    dur = float(ev.get("duration", 0))
                    elapsed = max(time.time() - self._chunk_started_at, 1e-3)
                    rate = len(chunk["text"]) / elapsed
                    self._rate_chars_per_s = (
                        rate if self._rate_chars_per_s == 0
                        else 0.8 * self._rate_chars_per_s + 0.2 * rate)
                    self.db.update_chunk(job_id, chunk["idx"], status="done",
                                         wav_path=ev["wav"], duration=dur,
                                         attempts=chunk["attempts"] + 1)
                    self.db.execute(
                        "UPDATE jobs SET done_chunks=done_chunks+1, "
                        "synth_seconds=synth_seconds+? WHERE id=?", (dur, job_id))
                    # Stream this chunk immediately (single worker => in order).
                    assembler.feed_wav(ev["wav"], bool(chunk["para_end"]),
                                       is_last=False)
                    mp3_streamer.feed_wav(ev["wav"], bool(chunk["para_end"]),
                                          is_last=False)
                    chunks_since_recycle += 1
                elif ev.get("event") == "chunk_failed":
                    log(f"chunk {chunk['idx']} failed in-engine: {ev.get('error')}")
                    self._register_chunk_failure(job_id, chunk, str(ev.get("error")), log)
                elif ev.get("event") == "fatal":
                    log(f"worker fatal: {ev.get('error')}")
                    worker.kill()
                    worker = None
                    self._register_chunk_failure(job_id, chunk, str(ev.get("error")), log)
        finally:
            self._current_chunk = None
            if worker:
                worker.quit_gracefully()
            # Close the muxer: flushes the last segment + writes #EXT-X-ENDLIST
            # if complete, or leaves a playable partial playlist otherwise.
            assembler.finalize()
            mp3_streamer.finalize()

        if self._shutdown.is_set():
            return

        failed = self.db.query(
            "SELECT idx, error FROM chunks WHERE job_id=? AND status='failed'", (job_id,))
        done = self.db.query(
            "SELECT * FROM chunks WHERE job_id=? AND status='done' ORDER BY idx", (job_id,))
        if not done:
            self.db.update_job(job_id, status="failed",
                               error="all chunks failed (see job log)")
            return
        job_now = self.db.job(job_id)
        job_now["output_dir"] = str(cdir)  # final M4B lives in the cache dir
        log(f"assembling {job_now.get('output_format') or 'mp3'} from "
            f"{len(done)} chunks ({len(failed)} failed)")
        from .audio import assemble
        try:
            mp3_path = assemble(job_now, done, work_dir)
        except Exception as exc:
            self.db.update_job(job_id, status="failed", error=f"assembly: {exc}")
            return
        if failed:
            report = Path(str(mp3_path) + ".skipped-chunks.txt")
            report.write_text(
                "\n".join(f"chunk {f['idx']}: {f['error']}" for f in failed),
                encoding="utf-8")
        total_s = sum(float(c.get("duration") or 0) for c in done)
        self.db.update_job(job_id, status="done", finished_at=time.time(),
                           failed_chunks=len(failed), output_mp3_path=str(mp3_path),
                           error=None)
        # Chunk WAVs are no longer needed: re-listening serves the finalized
        # HLS playlist (hls/) or the M4B straight from the cache dir.
        shutil.rmtree(work_dir / "chunks", ignore_errors=True)
        from . import cache
        self.db.upsert_cache(ckey, state="ready", duration_s=total_s,
                             size_bytes=cache.dir_size(cdir))
        cache.enforce_budget(self.db, keep_key=ckey)
        log(f"done -> {mp3_path}")

    def _spawn_ready_worker(self, job, voice, work_dir, log_file, log) -> Optional[WorkerHandle]:
        for attempt in range(2):
            w = WorkerHandle(job, voice, work_dir, log_file)
            ev = w.wait_event(config.MODEL_LOAD_TIMEOUT_S)
            if ev and ev.get("event") == "ready":
                return w
            log(f"worker start attempt {attempt + 1} failed: {ev}")
            w.kill()
        return None

    def _register_chunk_failure(self, job_id: int, chunk: dict, reason: str, log) -> None:
        attempts = chunk["attempts"] + 1
        if attempts >= config.CHUNK_MAX_ATTEMPTS:
            log(f"chunk {chunk['idx']}: giving up after {attempts} attempts — "
                f"continuing with the rest of the book")
            self.db.update_chunk(job_id, chunk["idx"], status="failed",
                                 attempts=attempts, error=reason)
            self.db.execute(
                "UPDATE jobs SET failed_chunks=failed_chunks+1 WHERE id=?", (job_id,))
        else:
            self.db.update_chunk(job_id, chunk["idx"], attempts=attempts, error=reason)

    def _start_caffeinate(self) -> None:
        if self._caffeinate and self._caffeinate.poll() is None:
            return
        if sys.platform == "darwin":
            try:
                self._caffeinate = subprocess.Popen(
                    ["caffeinate", "-ims"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:
                self._caffeinate = None

    def _stop_caffeinate(self) -> None:
        if self._caffeinate:
            try:
                self._caffeinate.terminate()
            except Exception:
                pass
            self._caffeinate = None
