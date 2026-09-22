from backend.intents import classify_intent, is_actionable
from backend.llm.provider import NullProvider
from backend.models import Intent, IntentResult


def test_skip_rule():
    result = classify_intent("skip", NullProvider())
    assert result.intent == Intent.SKIP
    assert result.confidence > 0.5


def test_restart_rule():
    result = classify_intent("I want to start over please", NullProvider())
    assert result.intent == Intent.RESTART


def test_undo_rule():
    result = classify_intent("undo that", NullProvider())
    assert result.intent == Intent.UNDO


def test_generate_post_rule():
    result = classify_intent("okay, write the post now", NullProvider())
    assert result.intent == Intent.GENERATE_POST


def test_revise_post_rule_extracts_instruction():
    result = classify_intent("can you make the post shorter", NullProvider())
    assert result.intent == Intent.REVISE_POST
    assert result.instruction


def test_falls_back_to_llm_for_ambiguous_text():
    # NullProvider always returns a low-confidence ANSWER for classification
    result = classify_intent("My mom had a difficult week but is improving.", NullProvider())
    assert result.intent == Intent.ANSWER


def test_is_actionable_blocks_low_confidence_destructive_intent():
    low_conf = IntentResult(intent=Intent.RESTART, confidence=0.5)
    assert not is_actionable(low_conf)
    high_conf = IntentResult(intent=Intent.RESTART, confidence=0.95)
    assert is_actionable(high_conf)


def test_is_actionable_allows_lower_bar_for_answer():
    ans = IntentResult(intent=Intent.ANSWER, confidence=0.4)
    assert not is_actionable(ans, min_confidence=0.55)
    ans2 = IntentResult(intent=Intent.ANSWER, confidence=0.6)
    assert is_actionable(ans2, min_confidence=0.55)
