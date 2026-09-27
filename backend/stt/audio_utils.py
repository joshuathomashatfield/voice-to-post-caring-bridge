"""
Audio normalization utilities.

Browsers typically produce WebM/Opus from MediaRecorder. faster-whisper (via
ffmpeg under the hood) can usually handle that directly, but we normalize
explicitly to mono 16kHz PCM WAV first so behavior is consistent across
browsers and so the VAD / duration checks operate on a known format.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import uuid

logger = logging.getLogger("caringbridge.audio")

TARGET_SAMPLE_RATE = 16000


class AudioValidationError(ValueError):
    pass


MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB safety ceiling


def validate_upload(raw_bytes: bytes, content_type: str) -> None:
    if not raw_bytes:
        raise AudioValidationError("Empty audio upload.")
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise AudioValidationError("Audio upload too large.")
    allowed_prefixes = ("audio/",)
    if content_type and not content_type.startswith(allowed_prefixes):
        raise AudioValidationError(f"Unexpected content type: {content_type}")


def normalize_to_wav(raw_bytes: bytes, suffix: str = ".webm") -> str:
    """Writes raw_bytes to a temp file, converts to mono 16kHz WAV via ffmpeg,
    returns the path to the normalized WAV file. Caller is responsible for
    deleting both temp files (see cleanup_temp_files)."""
    safe_suffix = suffix if suffix.startswith(".") and len(suffix) <= 6 else ".webm"
    tmp_dir = tempfile.gettempdir()
    input_path = os.path.join(tmp_dir, f"cb_in_{uuid.uuid4().hex}{safe_suffix}")
    output_path = os.path.join(tmp_dir, f"cb_out_{uuid.uuid4().hex}.wav")

    with open(input_path, "wb") as f:
        f.write(raw_bytes)

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE),
                "-f", "wav", output_path,
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        cleanup_temp_files(input_path, output_path)
        raise RuntimeError("ffmpeg is required but was not found on PATH.") from exc
    except subprocess.CalledProcessError as exc:
        cleanup_temp_files(input_path, output_path)
        logger.error("ffmpeg_failed stderr=%s", exc.stderr.decode(errors="ignore")[:500])
        raise RuntimeError("Could not process the audio recording.") from exc
    except subprocess.TimeoutExpired as exc:
        cleanup_temp_files(input_path, output_path)
        raise RuntimeError("Audio conversion timed out. Try a shorter recording.") from exc
    finally:
        cleanup_temp_files(input_path)

    return output_path


def cleanup_temp_files(*paths: str) -> None:
    """Delete temporary audio files. Called eagerly after transcription so
    microphone audio is not retained longer than necessary (see privacy.py
    and MASTER PROMPT section 14)."""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.warning("temp_cleanup_failed path=%s error=%s", path, exc)
