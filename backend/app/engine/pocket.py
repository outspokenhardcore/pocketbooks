"""Pocket TTS (Kyutai) on CPU.

The worker's ``ref_audio`` argument carries a built-in voice key such as
``marius``. Pocket TTS can clone voices, but PocketBooks deliberately exposes
only a fixed set of openly licensed voices (see NOTICE) and never consumes
reference recordings or transcripts.

When no Hugging Face token is present, ``pocket-tts`` loads the open
"without voice cloning" weights, so no account and no gated download is needed.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

from .. import config


class PocketEngine:
    def __init__(self, model: str = config.POCKET_MODEL):
        self.model_name = model
        self.model = None
        self.sample_rate = config.SAMPLE_RATE
        self._voice_cache: dict = {}

    def load(self) -> None:
        if self.model is not None:
            return
        from pocket_tts import TTSModel
        self.model = TTSModel.load_model(self.model_name)
        self.sample_rate = int(self.model.sample_rate)

    def _voice_state(self, voice: str):
        # Encoding a voice is costly; keep one state per voice for the worker's life.
        state = self._voice_cache.get(voice)
        if state is None:
            state = self.model.get_state_for_audio_prompt(voice)
            self._voice_cache[voice] = state
        return state

    def synthesize(self, text: str, ref_audio: str, ref_text: str,
                   language: str = "auto") -> Tuple[np.ndarray, int]:
        assert self.model is not None, "call load() first"
        audio = self.model.generate_audio(self._voice_state(ref_audio), text)
        out = np.asarray(audio.numpy(), dtype=np.float32).reshape(-1)
        return out, self.sample_rate
