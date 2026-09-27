from backend.models import QuestionStage, SessionState
from backend.state import SessionManager, session_manager


def test_create_and_get_session():
    s = session_manager.create()
    fetched = session_manager.get(s.session_id)
    assert fetched is not None
    assert fetched.session_id == s.session_id
    session_manager.clear(s.session_id)


def test_clear_removes_session():
    s = session_manager.create()
    session_manager.clear(s.session_id)
    assert session_manager.get(s.session_id) is None


def test_version_history_and_undo_redo():
    s = SessionState()
    v1 = SessionManager.add_version(s, "First draft.", source="generated")
    v2 = SessionManager.add_version(s, "Second draft.", source="revised")
    assert s.current_post_text == "Second draft."
    assert v1.version == 1 and v2.version == 2

    undone = SessionManager.undo(s)
    assert undone.text == "First draft."
    assert s.current_post_text == "First draft."

    redone = SessionManager.redo(s)
    assert redone.text == "Second draft."


def test_new_version_after_undo_truncates_redo_branch():
    s = SessionState()
    SessionManager.add_version(s, "v1", source="generated")
    SessionManager.add_version(s, "v2", source="revised")
    SessionManager.undo(s)  # back to v1
    SessionManager.add_version(s, "v3 (branched)", source="revised")
    assert len(s.post_versions) == 2
    assert s.current_post_text == "v3 (branched)"
    # nothing left to redo
    assert SessionManager.redo(s) is None


def test_default_stage_is_opening():
    s = SessionState()
    assert s.stage == QuestionStage.OPENING
