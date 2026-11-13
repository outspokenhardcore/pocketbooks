"""Engine interface. Implementations: Pocket TTS (production), fake (tests)."""
from __future__ import annotations

from typing import Protocol, Tuple

import numpy as np


class TTSEngine(Protocol):
    def load(self) -> None:
        """Load the model once. May take minutes (download is pre-flighted)."""
        ...

    def synthesize(self, text: str, ref_audio: str, ref_text: str,
                  language: str = "auto") -> Tuple[np.ndarray, int]:
        """Return (float32 mono audio, sample_rate)."""
        ...


def create_engine(kind: str) -> "TTSEngine":
    if kind == "pocket":
        from .pocket import PocketEngine
        return PocketEngine()
    if kind == "fake":
        from .fake import FakeEngine
        return FakeEngine()
    raise ValueError(f"unknown engine: {kind}")
