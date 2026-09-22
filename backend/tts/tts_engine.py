"""
Text-to-speech abstraction.

The default and recommended path is "browser" TTS: the frontend uses the
Web Speech API's SpeechSynthesis, which starts instantly, is trivially
interruptible, needs no server round-trip, and works with zero extra
dependencies (see frontend/app.js). This module exists so a server-side
engine (pyttsx3, Piper, Coqui) can be swapped in via TTS_ENGINE without
touching any calling code -- e.g. for headless/accessibility contexts where
browser speech synthesis isn't available or sounds too robotic.
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
import uuid
from abc import ABC, abstractmethod

from backend.config import settings

logger = logging.getLogger("caringbridge.tts")


class TTSEngine(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Returns audio bytes (WAV) for the given text."""
        raise NotImplementedError


class BrowserTTSEngine(TTSEngine):
    """No-op server-side stand-in: actual synthesis happens client-side via
    the Web Speech API. Present so the abstraction/config is uniform even
    when TTS_ENGINE=browser (the default)."""

    def synthesize(self, text: str) -> bytes:
        raise NotImplementedError(
            "TTS_ENGINE=browser performs synthesis in the browser; there is no "
            "server-side audio to generate. Use pyttsx3 or piper for server-side TTS."
        )


class Pyttsx3Engine(TTSEngine):
    def synthesize(self, text: str) -> bytes:
        import pyttsx3  # imported lazily -- optional dependency

        engine = pyttsx3.init()
        tmp_path = os.path.join(tempfile.gettempdir(), f"cb_tts_{uuid.uuid4().hex}.wav")
        try:
            engine.save_to_file(text, tmp_path)
            engine.runAndWait()
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class PiperEngine(TTSEngine):
    def synthesize(self, text: str) -> bytes:
        import subprocess

        if not settings.piper_voice_path:
            raise RuntimeError("PIPER_VOICE_PATH is not configured.")
        tmp_path = os.path.join(tempfile.gettempdir(), f"cb_tts_{uuid.uuid4().hex}.wav")
        try:
            subprocess.run(
                ["piper", "--model", settings.piper_voice_path, "--output_file", tmp_path],
                input=text.encode("utf-8"),
                check=True,
                capture_output=True,
                timeout=30,
            )
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


def get_tts_engine() -> TTSEngine:
    if settings.tts_engine == "pyttsx3":
        return Pyttsx3Engine()
    if settings.tts_engine == "piper":
        return PiperEngine()
    return BrowserTTSEngine()


def synthesize_to_base64(text: str) -> str:
    engine = get_tts_engine()
    audio_bytes = engine.synthesize(text)
    return base64.b64encode(audio_bytes).decode("ascii")
