"""Fake engine for tests — emits deterministic sine waves."""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np

from .. import config


class FakeEngine:
    def load(self) -> None:
        pass

    def synthesize(self, text: str, ref_audio: str, ref_text: str,
                  language: str = "auto") -> Tuple[np.ndarray, int]:
        chars = max(len(text), 1)
        duration = chars * 0.05
        t = np.linspace(0, duration, int(config.SAMPLE_RATE * duration))
        audio = (np.sin(2 * math.pi * 440 * t) * 0.3).astype(np.float32)
        return audio, config.SAMPLE_RATE
