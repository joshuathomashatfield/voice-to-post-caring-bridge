import json

from fastapi.testclient import TestClient

import app as server
from backend.conversation import extract_and_merge
from backend.llm.provider import LLMProvider, LLMUnavailableError
from backend.models import SessionState


class FakeLLM(LLMProvider):
    def __init__(self):
        self.messages = []

    def generate(self, messages, temperature=0.4):
        self.messages.append(messages)
        system = messages[0]['content']
        if 'intent classifier' in system:
            return '{"intent":"ANSWER","confidence":0.95}'
        if 'extract structured facts' in system:
            return json.dumps({'acknowledgement': "We can take this one step at a time.",
                               'reason_for_page': 'My sister is recovering from surgery.'})
        return 'My sister is recovering. Thank you for checking in.'


def setup_client(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(server, 'llm_provider', fake)
    client = TestClient(server.app)
    sid = client.post('/api/session').json()['session_id']
    return client, sid, fake


def test_answer_is_spoken_and_shared_state_is_visible(monkeypatch):
    client, sid, _ = setup_client(monkeypatch)
    data = client.post('/api/message', json={'session_id': sid, 'text': 'My sister is recovering.'}).json()
    assert data['speak'] == data['assistant_message'] + ' ' + data['next_question']
    assert data['shared_state']['context']['reason_for_page']
    assert data['stage'] == 'CURRENT_SITUATION'
    history = client.get(f'/api/state/{sid}').json()['conversation_history']
    assert history[-2]['content'] == data['assistant_message']
    assert history[-1]['content'] == data['next_question']


def test_manual_edit_is_revision_base_and_empty_edit_is_retained(monkeypatch):
    client, sid, fake = setup_client(monkeypatch)
    client.post('/api/manual-edit', json={'session_id': sid, 'text': 'My exact wording.'})
    client.post('/api/revise', json={'session_id': sid, 'instruction': 'make it warmer'})
    assert 'My exact wording.' in fake.messages[-1][1]['content']
    data = client.post('/api/manual-edit', json={'session_id': sid, 'text': ''}).json()
    assert data['post_text'] == ''
    assert client.get(f'/api/state/{sid}').json()['post_text'] == ''


def test_context_correction_marks_draft_stale_and_guides_regeneration(monkeypatch):
    client, sid, fake = setup_client(monkeypatch)
    client.post('/api/context', json={'session_id':sid, 'context':{'person':'Old name'}})
    client.post('/api/generate', json={'session_id':sid})
    updated = client.post('/api/context', json={'session_id':sid, 'context':{'person':None, 'tone':'brief'}}).json()
    assert updated['shared_state']['draft_needs_update']
    assert updated['post_text']  # Corrections do not silently overwrite the draft.
    generated = client.post('/api/generate', json={'session_id':sid}).json()
    assert not generated['shared_state']['draft_needs_update']
    prompt = fake.messages[-1][1]['content']
    assert '"person": null' in prompt
    assert '"tone": "brief"' in prompt


def test_restart_clears_shared_draft_and_stop_has_no_speech(monkeypatch):
    client, sid, _ = setup_client(monkeypatch)
    client.post('/api/manual-edit', json={'session_id':sid, 'text':'Old draft'})
    stop = client.post('/api/message', json={'session_id':sid, 'text':'stop speaking'}).json()
    assert stop['speak'] is None
    restart = client.post('/api/message', json={'session_id':sid, 'text':'start over'}).json()
    assert restart['post_text'] == ''
    assert restart['shared_state']['context']['person'] is None
    assert restart['shared_state']['version'] is None


def test_go_back_repeats_filled_question(monkeypatch):
    client, sid, _ = setup_client(monkeypatch)
    client.post('/api/message', json={'session_id':sid, 'text':'My sister is recovering.'})
    data = client.post('/api/message', json={'session_id':sid, 'text':'go back'}).json()
    assert data['stage'] == 'OPENING'
    assert 'who is this update about' in data['next_question']


def test_extraction_failure_preserves_uncertain_words_as_notes():
    class Offline(LLMProvider):
        def generate(self, *args, **kwargs):
            raise LLMUnavailableError('offline')
    session = SessionState()
    context, ok = extract_and_merge(session, 'What do you mean?', Offline())
    assert not ok
    assert context.reason_for_page is None
    assert context.additional_notes == ['What do you mean?']


def test_repeated_extraction_deduplicates_lists():
    class Repeat(LLMProvider):
        def generate(self, *args, **kwargs):
            return '{"support_requests":["meals"]}'
    session = SessionState()
    extract_and_merge(session, 'Meals would help.', Repeat())
    extract_and_merge(session, 'Meals would help.', Repeat())
    assert session.context.support_requests == ['meals']


def test_undo_to_older_context_is_marked_for_review(monkeypatch):
    client, sid, _ = setup_client(monkeypatch)
    client.post('/api/generate', json={'session_id':sid})
    client.post('/api/context', json={'session_id':sid, 'context':{'tone':'brief'}})
    client.post('/api/generate', json={'session_id':sid})
    restored = client.post('/api/undo', json={'session_id':sid}).json()
    assert restored['shared_state']['draft_needs_update']
