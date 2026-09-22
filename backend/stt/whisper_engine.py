"""
Speech-to-text engine backed by faster-whisper, running locally.

The model is loaded once and cached (see get_whisper_model) rather than
reloaded per request -- Whisper model load time is significant and doing it
per-turn would make every voice interaction feel sluggish.

Reliability handling per MASTER PROMPT sections 7-8:
- raw_transcript and clean_transcript are both kept.
- confidence heuristics (avg logprob, no_speech_prob, duration, VAD) decide
  whether a transcript is "reliable enough" to feed into the conversation,
  vs. asking the user to repeat themselves.
"""
from __future__ import annotations

import logging
import re
import time
from functools import lru_cache

from backend.config import settings
from backend.models import TranscriptResult
from backend.stt.vad import detect_speech

logger = logging.getLogger("caringbridge.stt")

_FILLER_ARTIFACT_PATTERNS = [
    re.compile(r"\b(thank you for watching!?)\b", re.I),  # common Whisper hallucination on silence
    re.compile(r"\b(subtitles by .*)\b", re.I),
    re.compile(r"\s{2,}"),
]


@lru_cache(maxsize=1)
def get_whisper_model():
    from faster_whisper import WhisperModel  # imported lazily: heavy, optional at import time
    logger.info("loading_whisper_model model=%s device=%s compute_type=%s",
                settings.stt_model, settings.stt_device, settings.stt_compute_type)
    return WhisperModel(settings.stt_model, device=settings.stt_device, compute_type=settings.stt_compute_type)


def _clean_transcript(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"([!?.]){2,}", r"\1", cleaned)
    for pattern in _FILLER_ARTIFACT_PATTERNS[:2]:
        cleaned = pattern.sub("", cleaned).strip()
    return cleaned


def transcribe(wav_path: str) -> TranscriptResult:
    """Runs VAD first (cheap) to short-circuit on silence/near-silence, then
    Whisper transcription with confidence heuristics."""
    vad_result = detect_speech(wav_path)
    if vad_result.total_seconds > settings.max_recording_seconds + 2:
        logger.warning("recording_exceeds_max_duration seconds=%.1f", vad_result.total_seconds)

    if not vad_result.has_speech or vad_result.speech_seconds < settings.min_speech_seconds:
        return TranscriptResult(
            raw_transcript="",
            clean_transcript="",
            confidence=0.0,
            is_reliable=False,
            no_speech_probability=1.0,
            duration_seconds=vad_result.total_seconds,
        )

    model = get_whisper_model()
    start = time.time()
    segments, info = model.transcribe(
        wav_path,
        language=settings.stt_language,
        vad_filter=True,
        beam_size=5,
    )
    segments = list(segments)
    duration = time.time() - start

    raw_text = " ".join(seg.text.strip() for seg in segments).strip()
    avg_logprob = sum(seg.avg_logprob for seg in segments) / len(segments) if segments else -10.0
    no_speech_prob = max((seg.no_speech_prob for seg in segments), default=1.0)

    # Rough, defensible confidence heuristic: avg_logprob close to 0 is
    # confident; very negative is not. Combine with no_speech_prob and
    # transcript length.
    confidence = max(0.0, min(1.0, 1.0 + (avg_logprob / 5.0))) * (1.0 - no_speech_prob)
    clean_text = _clean_transcript(raw_text)
    is_reliable = (
        bool(re.search(r"\w", clean_text))
        and no_speech_prob < 0.6
        and confidence > 0.35
    )

    logger.info("stt_complete duration=%.2fs chars=%d confidence=%.2f",
                duration, len(raw_text), confidence)

    return TranscriptResult(
        raw_transcript=raw_text,
        clean_transcript=clean_text,
        confidence=confidence,
        is_reliable=is_reliable,
        no_speech_probability=no_speech_prob,
        duration_seconds=vad_result.total_seconds,
    )
