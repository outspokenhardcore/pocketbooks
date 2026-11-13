# Third-party notices

PocketBooks is licensed under the [AGPL-3.0](LICENSE). The components below keep
their own licenses.

## Text to speech

| Component | License | Notes |
|---|---|---|
| [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (Python package) | MIT | Kyutai |
| [`kyutai/pocket-tts-without-voice-cloning`](https://huggingface.co/kyutai/pocket-tts-without-voice-cloning) (model weights) | CC BY 4.0 | Kyutai. Downloaded at first use, never shipped in this repository. |

Kyutai's model card lists prohibited uses, among them voice impersonation or
cloning without explicit and lawful consent, deception, and unlawful or harmful
content. PocketBooks offers no voice cloning and no way to upload a reference
voice. If you build on it, keep it that way and follow those terms.

## Voices

PocketBooks exposes exactly these voices from
[`kyutai/tts-voices`](https://huggingface.co/kyutai/tts-voices). Every one of
them permits commercial use.

| Voice in the app | Source set | License |
|---|---|---|
| Marius | Unmute Voice Donation Project | CC0 |
| Javert | Unmute Voice Donation Project | CC0 |
| Peter | Voice-Zero (LibriVox), Peter Yearsley | CC0 |
| Bill | Voice-Zero (LibriVox), Bill Boerst | CC0 |
| Caro | Voice-Zero (LibriVox), Caro Davy | CC0 |
| Alba | Alba MacKenna | **CC BY 4.0**: credit Alba MacKenna |

The non-commercial voice sets in that repository (Expresso, EARS) are left out
on purpose. Do not add them to a build you distribute commercially.

## Sample books

Public-domain texts, with the rights record per title in
[`samples/README.md`](samples/README.md).

## Python dependencies

PyMuPDF is AGPL-3.0 / commercial dual licensed, see
[pymupdf.io/licensing](https://pymupdf.io/licensing). The other runtime
dependencies (FastAPI, Uvicorn, Pydantic, NumPy, SciPy, SoundFile, Mutagen,
PyTorch) are under permissive licenses.
