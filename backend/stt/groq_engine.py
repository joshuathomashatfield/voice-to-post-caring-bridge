"""Groq-hosted speech-to-text for serverless deployments.

This path deliberately sends the browser's original WebM/MP4/OGG upload
straight to Groq. Groq accepts those formats and performs its own audio
normalization, so Vercel does not need FFmpeg or a local Whisper model.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

import httpx

from backend.config import settings
from backend.models import TranscriptResult

logger = logging.getLogger("caringbridge.stt.groq")

_FILLER_ARTIFACT_PATTERNS = [
    re.compile(r"\b(thank you for watching!?)\b", re.I),
    re.compile(r"\b(subtitles by .*)\b", re.I),
]


def _clean_transcript(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"([!?.]){2,}", r"\1", cleaned)
    for pattern in _FILLER_ARTIFACT_PATTERNS:
        cleaned = pattern.sub("", cleaned).strip()
    return cleaned


def transcribe_bytes(
    raw_bytes: bytes,
    filename: str = "turn.webm",
    content_type: Optional[str] = "audio/webm",
) -> TranscriptResult:
    """Transcribe an uploaded recording with Groq Whisper.

    Groq's JSON response does not expose the same per-segment probability
    fields used by faster-whisper, so reliability is intentionally
    conservative: an empty/non-word transcript is rejected; otherwise the
    transcript is accepted and the conversation layer handles clarification.
    """
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")

    safe_name = os.path.basename(filename or "turn.webm")
    url = f"{settings.groq_base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}
    data = {
        "model": settings.groq_stt_model,
        "language": settings.stt_language,
        "response_format": "json",
        "temperature": "0",
    }
    files = {
        "file": (safe_name, raw_bytes, content_type or "application/octet-stream"),
    }

    with httpx.Client(timeout=settings.stt_request_timeout) as client:
        response = client.post(url, headers=headers, data=data, files=files)
        response.raise_for_status()
        payload = response.json()

    raw_text = str(payload.get("text", "") or "").strip()
    clean_text = _clean_transcript(raw_text)
    has_words = bool(re.search(r"\w", clean_text))
    logger.info("groq_stt_complete chars=%d reliable=%s", len(raw_text), has_words)

    return TranscriptResult(
        raw_transcript=raw_text,
        clean_transcript=clean_text,
        confidence=1.0 if has_words else 0.0,
        is_reliable=has_words,
        no_speech_probability=0.0 if has_words else 1.0,
        duration_seconds=0.0,
    )
