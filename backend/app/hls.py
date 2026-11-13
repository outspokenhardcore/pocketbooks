"""Progressive HLS streaming assembler + growing MP3 file for direct download.

Because the server edition runs a SINGLE worker, chunks finish strictly in
order (idx 0,1,2,...). That lets us feed their raw PCM, in order, into one
long-running ffmpeg muxer which does all the hard parts itself:

    raw s16le PCM (in order)  ->  ffmpeg  ->  init.mp4 + seg_NNNNN.m4s
                                              + growing index.m3u8 (EVENT)

ffmpeg appends each finished segment to the EVENT playlist; on stdin EOF it
writes #EXT-X-ENDLIST. iOS AVPlayer can start playing as soon as the first
segment is listed.

For offline download, MP3Streamer writes the same raw PCM into a single
growing MP3 file that can be streamed to the client via chunked HTTP while
the generation is still in progress.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Optional

from . import config

PLAYLIST_NAME = "index.m3u8"
INIT_NAME = "init.mp4"


def _gap_pcm(seconds: float) -> bytes:
    """Silence as s16 mono PCM at the project sample rate."""
    n = int(seconds * config.SAMPLE_RATE)
    return b"\x00\x00" * n  # 2 bytes per sample, mono


class StreamAssembler:
    """One ffmpeg HLS muxer fed PCM in chunk order. Reusable per job run."""

    def __init__(self, stream_dir: Path):
        self.dir = stream_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.proc: Optional[subprocess.Popen] = None

    @property
    def playlist(self) -> Path:
        return self.dir / PLAYLIST_NAME

    def start(self) -> None:
        cmd = [
            config.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(config.SAMPLE_RATE), "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "aac", "-b:a", config.HLS_AAC_BITRATE,
            "-f", "hls",
            "-hls_time", str(config.TARGET_SEGMENT_S),
            "-hls_playlist_type", "event",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", INIT_NAME,
            "-hls_segment_filename", str(self.dir / "seg_%05d.m4s"),
            "-hls_list_size", "0",
            str(self.playlist),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def _write(self, data: bytes) -> None:
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(data)

    def feed_wav(self, wav_path: str, para_end: bool, is_last: bool) -> None:
        """Append one chunk's audio (+ the same inter-chunk gap as the M4B)."""
        if not self.proc:
            self.start()
        try:
            with wave.open(str(wav_path), "rb") as w:
                self._write(w.readframes(w.getnframes()))
        except (FileNotFoundError, wave.Error, OSError):
            return
        if not is_last:
            gap = config.GAP_PARAGRAPH_S if para_end else config.GAP_SENTENCE_S
            self._write(_gap_pcm(gap))

    def finalize(self) -> None:
        """Close stdin so ffmpeg flushes the last segment + writes ENDLIST."""
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        finally:
            self.proc = None

    def abort(self) -> None:
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None

    def is_ready_to_play(self) -> bool:
        """True once at least one segment is listed in the playlist."""
        try:
            return ".m4s" in self.playlist.read_text(encoding="utf-8")
        except OSError:
            return False


class MP3Streamer:
    """Like StreamAssembler, but writes a single growing MP3 file instead of
    HLS segments. The file is valid MP3 from the first chunk on, so clients
    can stream it while it's still being written.

    Example:
        s = MP3Streamer(Path("/tmp/job_42/output.mp3"))
        s.start()
        s.feed_wav("/tmp/job_42/work/chunks/0000.wav", para_end=True)
        ... more chunks ...
        s.finalize()
    """

    def __init__(self, mp3_path: Path):
        self.path = mp3_path
        self.proc: Optional[subprocess.Popen] = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        cmd = [
            config.ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(config.SAMPLE_RATE), "-ac", "1",
            "-i", "pipe:0",
            "-c:a", "libmp3lame", "-b:a", "64k",
            "-f", "mp3",
            str(self.path),
        ]
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def _write(self, data: bytes) -> None:
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(data)
            except BrokenPipeError:
                pass

    def feed_wav(self, wav_path: str, para_end: bool, is_last: bool) -> None:
        if not self._started:
            self.start()
        try:
            with wave.open(str(wav_path), "rb") as w:
                self._write(w.readframes(w.getnframes()))
        except (FileNotFoundError, wave.Error, OSError):
            return

    def finalize(self) -> None:
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        finally:
            self.proc = None

    def abort(self) -> None:
        if self.proc:
            try:
                self.proc.kill()
            except OSError:
                pass
            self.proc = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
