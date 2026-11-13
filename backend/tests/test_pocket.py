"""Pocket TTS-specific configuration, fixed voices, and engine adapter tests."""
import sys
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(db):
    from app import config
    from app.server import create_app
    from app.supervisor import Supervisor
    result = TestClient(create_app(db, Supervisor(db)))
    result.headers.update({
        "Authorization": f"Bearer {config.get_or_create_token()}"})
    return result


def test_builtin_voices_have_stable_ids(db):
    voices = db.voices()
    assert [(v["id"], v["name"], v["ref_wav_path"]) for v in voices] == [
        (1, "Marius", "marius"),
        (2, "Javert", "javert"),
        (3, "Peter", "peter_yearsley"),
        (4, "Bill", "bill_boerst"),
        (5, "Caro", "caro_davy"),
        (6, "Alba", "alba"),
    ]
    assert all(v["source_path"] == "builtin:pocket" for v in voices)


def test_voice_api_is_read_only(client):
    # The iOS API client decodes this endpoint as a plain array.
    assert [v["id"] for v in client.get("/api/voices").json()] == [1, 2, 3, 4, 5, 6]
    assert client.post("/api/voices", json={}).status_code == 405
    assert client.patch("/api/voices/1", json={"name": "Changed"}).status_code in (404, 405)
    assert client.delete("/api/voices/1").status_code in (404, 405)


def test_pocket_engine_loads_and_caches_voice_states(monkeypatch):
    calls = {"states": [], "generated": []}

    class Audio:
        def __init__(self, values):
            self._values = values

        def numpy(self):
            return np.asarray(self._values, dtype=np.float32)

    class Model:
        sample_rate = 24000

        def get_state_for_audio_prompt(self, voice):
            calls["states"].append(voice)
            return f"state:{voice}"

        def generate_audio(self, state, text):
            calls["generated"].append((state, text))
            return Audio([0.1, 0.2, 0.3])

    module = types.ModuleType("pocket_tts")
    module.TTSModel = types.SimpleNamespace(load_model=lambda name: Model())
    monkeypatch.setitem(sys.modules, "pocket_tts", module)

    from app.engine.pocket import PocketEngine
    engine = PocketEngine("english")
    engine.load()
    audio, sample_rate = engine.synthesize("Hello", "marius", "ignored")
    engine.synthesize("Again", "marius", "ignored")
    assert sample_rate == 24000
    assert np.allclose(audio, [0.1, 0.2, 0.3])
    assert calls["states"] == ["marius"]  # encoded once, reused
    assert calls["generated"][0] == ("state:marius", "Hello")


def test_separate_defaults_and_shared_library(env, monkeypatch):
    from app import config
    assert config.APP_NAME == "PocketBooks"
    assert config.SERVER_PORT == 8725
    assert config.POCKET_MODEL == "english"
