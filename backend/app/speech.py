"""Server-side speech-to-text with Whisper (faster-whisper), fully local.

Optional: install requirements-voice.txt. Without it the frontend uses the
browser's built-in Web Speech API instead.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading

from .medical_terms import get_kb

log = logging.getLogger("medvoice.speech")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
_model = None
_lock = threading.Lock()


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            log.info("Loading Whisper model '%s' (first call can take a while)...", WHISPER_MODEL)
            _model = WhisperModel(WHISPER_MODEL, device=os.getenv("WHISPER_DEVICE", "cpu"),
                                  compute_type=os.getenv("WHISPER_COMPUTE", "int8"))
        return _model


def transcribe(audio: bytes, lang: str = "fr", suffix: str = ".webm") -> dict:
    model = _get_model()
    # Bias the decoder toward drug names so they are spelled correctly.
    prompt = ("Ordonnance médicale : " if lang == "fr" else "Medical prescription: ") + get_kb().vocabulary_prompt(lang, 80)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        segments, info = model.transcribe(path, language=lang, initial_prompt=prompt, vad_filter=True, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()
    finally:
        os.unlink(path)
    return {"text": text, "language": info.language, "duration": round(info.duration, 1)}
