import pytest

from backend.stt.audio_utils import AudioValidationError, validate_upload


def test_rejects_empty_upload():
    with pytest.raises(AudioValidationError):
        validate_upload(b"", "audio/webm")


def test_rejects_oversized_upload():
    with pytest.raises(AudioValidationError):
        validate_upload(b"0" * (26 * 1024 * 1024), "audio/webm")


def test_rejects_wrong_content_type():
    with pytest.raises(AudioValidationError):
        validate_upload(b"not empty", "text/plain")


def test_accepts_reasonable_audio_upload():
    validate_upload(b"some bytes of audio", "audio/webm")
