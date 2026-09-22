"""
Voice-activity detection.

Prefers `webrtcvad` when installed (a thin, fast C-based VAD from Google);
falls back to a simple energy-based heuristic on raw PCM samples when it
isn't available, so the app still runs without an extra native dependency.
Either way, this is used to reject empty/near-empty/very short clips before
they're ever sent to Whisper -- avoiding wasted transcription calls on
silence, room noise, or accidental taps.
"""
from __future__ import annotations

import array
import logging
import wave
from dataclasses import dataclass

logger = logging.getLogger("caringbridge.vad")

try:
    import webrtcvad
    _HAS_WEBRTCVAD = True
except ImportError:
    _HAS_WEBRTCVAD = False


@dataclass
class VadResult:
    has_speech: bool
    speech_seconds: float
    total_seconds: float


def _read_wav_pcm16(path: str):
    with wave.open(path, "rb") as wf:
        assert wf.getsampwidth() == 2, "Expected 16-bit PCM WAV"
        n_channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)
    samples = array.array("h", raw)
    if n_channels > 1:
        samples = array.array("h", samples[::n_channels])
    return samples, sample_rate


def _energy_based_vad(samples: array.array, sample_rate: int, frame_ms: int = 30,
                       energy_threshold: float = 250.0) -> VadResult:
    frame_len = int(sample_rate * frame_ms / 1000)
    if frame_len <= 0 or len(samples) == 0:
        return VadResult(has_speech=False, speech_seconds=0.0, total_seconds=0.0)

    speech_frames = 0
    total_frames = 0
    for start in range(0, len(samples) - frame_len, frame_len):
        frame = samples[start:start + frame_len]
        rms = (sum(s * s for s in frame) / len(frame)) ** 0.5
        total_frames += 1
        if rms > energy_threshold:
            speech_frames += 1

    total_seconds = len(samples) / sample_rate
    speech_seconds = speech_frames * (frame_ms / 1000)
    return VadResult(
        has_speech=speech_frames >= 3,  # require a little sustained energy, not one blip
        speech_seconds=speech_seconds,
        total_seconds=total_seconds,
    )


def _webrtcvad_based(samples: array.array, sample_rate: int, frame_ms: int = 30,
                      aggressiveness: int = 2) -> VadResult:
    vad = webrtcvad.Vad(aggressiveness)
    frame_len = int(sample_rate * frame_ms / 1000)
    speech_frames = 0
    total_frames = 0
    for start in range(0, len(samples) - frame_len, frame_len):
        frame = samples[start:start + frame_len].tobytes()
        total_frames += 1
        try:
            if vad.is_speech(frame, sample_rate):
                speech_frames += 1
        except Exception:  # noqa: BLE001 -- malformed frame, just skip it
            continue

    total_seconds = len(samples) / sample_rate
    speech_seconds = speech_frames * (frame_ms / 1000)
    return VadResult(
        has_speech=speech_frames >= 3,
        speech_seconds=speech_seconds,
        total_seconds=total_seconds,
    )


def detect_speech(wav_path: str) -> VadResult:
    samples, sample_rate = _read_wav_pcm16(wav_path)
    if _HAS_WEBRTCVAD and sample_rate in (8000, 16000, 32000, 48000):
        try:
            return _webrtcvad_based(samples, sample_rate)
        except Exception as exc:  # noqa: BLE001
            logger.warning("webrtcvad_failed_falling_back error=%s", exc)
    return _energy_based_vad(samples, sample_rate)
