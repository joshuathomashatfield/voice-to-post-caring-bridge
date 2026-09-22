from types import SimpleNamespace
from fastapi.testclient import TestClient
import app as server
from backend.models import TranscriptResult
from backend.llm.provider import NullProvider
from backend.stt import whisper_engine


def test_voice_upload_reaches_conversation_and_cleans_audio(monkeypatch):
    monkeypatch.setattr(server, 'llm_provider', NullProvider())
    monkeypatch.setattr(server, 'normalize_to_wav', lambda *args, **kwargs: 'fake.wav')
    monkeypatch.setattr(server, 'whisper_transcribe', lambda path: TranscriptResult(
        raw_transcript='skip', clean_transcript='skip', is_reliable=True))
    cleaned = []
    monkeypatch.setattr(server, 'cleanup_temp_files', lambda path: cleaned.append(path))
    client = TestClient(server.app)
    sid = client.post('/api/session').json()['session_id']
    response = client.post('/api/audio', data={'session_id':sid}, files={'file':('turn.webm', b'bytes', 'audio/webm')})
    assert response.status_code == 200
    assert response.json()['intent'] == 'SKIP'
    assert response.json()['speak']
    assert cleaned == ['fake.wav']


def test_missing_ffmpeg_has_specific_error(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError('ffmpeg missing')
    monkeypatch.setattr(server, 'normalize_to_wav', unavailable)
    monkeypatch.setattr(server.shutil, 'which', lambda name: None)
    client = TestClient(server.app)
    sid = client.post('/api/session').json()['session_id']
    data = client.post('/api/audio', data={'session_id':sid}, files={'file':('turn.webm',b'bytes','audio/webm')}).json()
    assert not data['transcript_reliable']
    assert 'FFmpeg is missing' in data['error']


def test_frontend_prevents_stale_assets():
    client = TestClient(server.app)
    for url in ['/', '/frontend/app.js?v=micfix-2', '/frontend/audio.js?v=micfix-2']:
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'


def test_single_word_transcript_can_be_reliable(monkeypatch):
    monkeypatch.setattr(whisper_engine, 'detect_speech', lambda path: SimpleNamespace(
        total_seconds=1, speech_seconds=.8, has_speech=True))
    monkeypatch.setattr(whisper_engine, 'get_whisper_model', lambda: SimpleNamespace(
        transcribe=lambda *args, **kwargs: ([SimpleNamespace(text='Skip.', avg_logprob=-.1, no_speech_prob=.01)],None)))
    transcript = whisper_engine.transcribe('fake.wav')
    assert transcript.is_reliable
    assert transcript.clean_transcript == 'Skip.'
