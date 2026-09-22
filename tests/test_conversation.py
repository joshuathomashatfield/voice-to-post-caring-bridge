from backend.conversation import advance_after_answer, go_back, next_question
from backend.models import QuestionStage, SessionState


def test_first_question_is_opening():
    s = SessionState()
    q = next_question(s)
    assert q is not None
    assert s.stage == QuestionStage.OPENING


def test_skips_stage_once_context_filled():
    s = SessionState()
    s.context.reason_for_page = "My sister was in a car accident."
    q = next_question(s)
    assert s.stage == QuestionStage.CURRENT_SITUATION
    assert q is not None


def test_advance_after_answer_moves_forward():
    s = SessionState()
    s.stage = QuestionStage.OPENING
    advance_after_answer(s)
    assert s.stage == QuestionStage.CURRENT_SITUATION


def test_go_back_returns_to_previous_stage():
    s = SessionState()
    s.stage = QuestionStage.OPENING
    advance_after_answer(s)
    assert s.stage == QuestionStage.CURRENT_SITUATION
    go_back(s)
    assert s.stage == QuestionStage.OPENING


def test_ready_to_draft_when_all_stages_filled():
    s = SessionState()
    s.context.reason_for_page = "reason"
    s.context.current_situation = "situation"
    s.context.current_status = "status"
    s.context.communication_preferences = "check the page"
    s.context.tone = "warm"
    s.meaningful_answers = 5
    q = next_question(s)
    assert q is None
    assert s.stage == QuestionStage.READY_TO_DRAFT
