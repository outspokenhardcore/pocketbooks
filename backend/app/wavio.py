"""Tiny stdlib WAV I/O (mono 16-bit PCM) — avoids a soundfile dependency in
the core pipeline; the TTS engine brings its own audio stack inside the worker."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def write_wav(path: str | Path, audio: np.ndarray, sr: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def wav_info(path: str | Path) -> tuple[int, float]:
    """Return (sample_rate, duration_seconds)."""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        return sr, w.getnframes() / float(sr)
