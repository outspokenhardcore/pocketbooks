"""Progressive HLS assembler — exercised against real ffmpeg."""
import shutil

import numpy as np
import pytest

from app import config
from app.hls import StreamAssembler, _gap_pcm
from app.wavio import write_wav

ffmpeg_missing = shutil.which(config.ffmpeg_path()) is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not installed")


def _tone(path, seconds=6.0, hz=220):
    sr = config.SAMPLE_RATE
    a = (0.2 * np.sin(2 * np.pi * hz * np.arange(int(sr * seconds)) / sr)).astype("float32")
    write_wav(path, a, sr)


def test_gap_pcm_length():
    # s16 mono => 2 bytes per sample.
    assert len(_gap_pcm(1.0)) == config.SAMPLE_RATE * 2
    assert len(_gap_pcm(0.0)) == 0


def test_assembler_produces_playlist_and_segments(env, tmp_path):
    wavs = []
    for i in range(3):
        p = tmp_path / f"c{i}.wav"
        _tone(p)
        wavs.append(p)

    sd = tmp_path / "hls"
    asm = StreamAssembler(sd)
    for i, p in enumerate(wavs):
        asm.feed_wav(str(p), para_end=(i == 1), is_last=(i == len(wavs) - 1))
    asm.finalize()

    playlist = (sd / "index.m3u8").read_text()
    assert "#EXTM3U" in playlist
    assert "#EXT-X-MAP:URI=\"init.mp4\"" in playlist
    assert "#EXT-X-ENDLIST" in playlist
    assert (sd / "init.mp4").exists()
    segs = list(sd.glob("*.m4s"))
    # ~18s of audio at 10s target => 2 segments.
    assert len(segs) >= 2
    assert asm.is_ready_to_play()


def test_feed_missing_wav_is_ignored(env, tmp_path):
    sd = tmp_path / "hls"
    asm = StreamAssembler(sd)
    asm.feed_wav(str(tmp_path / "nope.wav"), para_end=False, is_last=True)
    asm.finalize()  # must not raise even though nothing was fed
