"""
Conversation policy: which question to ask next, and how to turn a user's
free-text answer into structured PostContext fields.

The question SEQUENCE is fixed and small (this is a conversation, not a
survey), but any stage is skipped automatically once enough information for
it already exists -- see `next_question`. The LLM is used only to extract
structured facts from what the user actually said; it is never allowed to
invent a stage outside this predefined set (see MASTER PROMPT section 21).
"""
from __future__ import annotations

import json
import logging
from typing import Optional, Tuple

from pydantic import ValidationError

from backend.config import settings
from backend.llm.provider import LLMProvider, LLMUnavailableError
from backend.models import ExtractedInfo, PostContext, QuestionStage, SessionState

logger = logging.getLogger("caringbridge.conversation")

WELCOME_MESSAGE = (
    "I'm here to help you write your first CaringBridge update. There's no "
    "right way to do this -- I'll ask a few short questions, and you can "
    "answer by speaking or typing, skip anything you'd rather not answer, "
    "and change your mind at any point. Whenever you're ready, let's start."
)

_QUESTIONS = {
    QuestionStage.OPENING: (
        "To start, who is this update about, and what would you like friends "
        "and family to know about why you're creating this page?"
    ),
    QuestionStage.CURRENT_SITUATION: (
        "Thank you. What's been happening recently, or what's the current situation?"
    ),
    QuestionStage.HOW_DOING: (
        "How are things going right now, generally speaking?"
    ),
    QuestionStage.SUPPORT: (
        "Is there anything people can do right now to support you or your family -- "
        "or would you rather keep this update informational for now?"
    ),
    QuestionStage.COMMUNICATION: (
        "Would you like people to check back here for updates instead of messaging "
        "you directly, or do you have another preference?"
    ),
    QuestionStage.CLOSING_TONE: (
        "Last thing -- is there a particular tone you'd like for the post, such as "
        "warm, brief, hopeful, or straightforward? Or I can just match how you've "
        "been talking about it."
    ),
}

_STAGE_ORDER = [
    QuestionStage.OPENING,
    QuestionStage.CURRENT_SITUATION,
    QuestionStage.HOW_DOING,
    QuestionStage.SUPPORT,
    QuestionStage.COMMUNICATION,
    QuestionStage.CLOSING_TONE,
    QuestionStage.READY_TO_DRAFT,
]

_EXTRACTION_SYSTEM_PROMPT = """You extract structured facts from what a user says while preparing their
first CaringBridge health-update post. You must NEVER invent, infer, or embellish
information the user did not state. If something wasn't mentioned, leave it null
or as an empty list.

Return ONLY minified JSON (no markdown fences, no commentary) matching exactly:
{"person": str|null, "relationship": str|null, "reason_for_page": str|null,
"current_situation": str|null, "current_status": str|null,
"important_updates": [str], "support_requests": [str],
"communication_preferences": str|null, "tone": str|null,
"privacy_constraints": [str], "additional_notes": [str]}

Only fill fields that are actually supported by the user's message. Do not
diagnose medical conditions, guess prognosis, or assume religious/emotional
content. If the user asks to omit or not mention something, put a short
description of it in "privacy_constraints".
"""


def next_question(session: SessionState) -> Optional[str]:
    """Return the next question to ask given current stage/context, skipping
    stages that already have sufficient information, per the policy in
    MASTER PROMPT section 21."""
    ctx = session.context
    stage = session.stage

    while True:
        if stage == QuestionStage.OPENING and ctx.reason_for_page:
            stage = QuestionStage.CURRENT_SITUATION
            continue
        if stage == QuestionStage.CURRENT_SITUATION and ctx.current_situation:
            stage = QuestionStage.HOW_DOING
            continue
        if stage == QuestionStage.HOW_DOING and ctx.current_status:
            stage = QuestionStage.SUPPORT
            continue
        if stage == QuestionStage.SUPPORT and (ctx.support_requests or session.meaningful_answers >= 4):
            stage = QuestionStage.COMMUNICATION
            continue
        if stage == QuestionStage.COMMUNICATION and (ctx.communication_preferences or session.meaningful_answers >= 5):
            stage = QuestionStage.CLOSING_TONE
            continue
        if stage == QuestionStage.CLOSING_TONE and ctx.tone:
            stage = QuestionStage.READY_TO_DRAFT
            continue
        break

    session.stage = stage
    if stage == QuestionStage.READY_TO_DRAFT or stage == QuestionStage.DONE:
        return None
    return _QUESTIONS[stage]


def advance_after_answer(session: SessionState) -> None:
    """Move to the next stage in sequence after a user has answered (used for
    SKIP, or simply to progress the conversation forward)."""
    idx = _STAGE_ORDER.index(session.stage) if session.stage in _STAGE_ORDER else 0
    if idx < len(_STAGE_ORDER) - 1:
        session.stage_history.append(session.stage)
        session.stage = _STAGE_ORDER[idx + 1]


def go_back(session: SessionState) -> None:
    if session.stage_history:
        session.stage = session.stage_history.pop()


def extract_and_merge(session: SessionState, user_text: str, llm: LLMProvider) -> Tuple[PostContext, bool]:
    """Extract structured info from the user's answer and merge it into the
    session's PostContext. Returns (context, extraction_succeeded)."""
    try:
        raw = llm.generate([
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Current question stage: {session.stage.value}\n"
                f"User said: {user_text}"
            )},
        ], temperature=0.1)
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned) if cleaned else {}
        extracted = ExtractedInfo(**data)
    except (LLMUnavailableError, json.JSONDecodeError, ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("extraction_failed stage=%s error=%s", session.stage, exc)
        return _fallback_merge(session, user_text), False

    ctx = session.context
    for field in ("person", "relationship", "reason_for_page", "current_situation",
                  "current_status", "communication_preferences", "tone"):
        value = getattr(extracted, field)
        if value:
            setattr(ctx, field, value)
    ctx.important_updates.extend(extracted.important_updates)
    ctx.support_requests.extend(extracted.support_requests)
    ctx.privacy_constraints.extend(extracted.privacy_constraints)
    ctx.additional_notes.extend(extracted.additional_notes)

    if not _extraction_touched_current_stage(session.stage, extracted):
        _fallback_merge(session, user_text)

    return ctx, True


def _extraction_touched_current_stage(stage: QuestionStage, extracted: ExtractedInfo) -> bool:
    mapping = {
        QuestionStage.OPENING: bool(extracted.reason_for_page or extracted.person or extracted.relationship),
        QuestionStage.CURRENT_SITUATION: bool(extracted.current_situation or extracted.important_updates),
        QuestionStage.HOW_DOING: bool(extracted.current_status or extracted.important_updates),
        QuestionStage.SUPPORT: bool(extracted.support_requests),
        QuestionStage.COMMUNICATION: bool(extracted.communication_preferences),
        QuestionStage.CLOSING_TONE: bool(extracted.tone),
    }
    return mapping.get(stage, True)


def _fallback_merge(session: SessionState, user_text: str) -> PostContext:
    """If structured extraction fails or misses the current stage entirely,
    fall back to storing the raw answer directly in the field the current
    question was aimed at, so no information is silently lost."""
    ctx = session.context
    stage = session.stage
    text = user_text.strip()
    if not text:
        return ctx
    if stage == QuestionStage.OPENING and not ctx.reason_for_page:
        ctx.reason_for_page = text
    elif stage == QuestionStage.CURRENT_SITUATION and not ctx.current_situation:
        ctx.current_situation = text
    elif stage == QuestionStage.HOW_DOING and not ctx.current_status:
        ctx.current_status = text
    elif stage == QuestionStage.SUPPORT:
        ctx.support_requests.append(text)
    elif stage == QuestionStage.COMMUNICATION and not ctx.communication_preferences:
        ctx.communication_preferences = text
    elif stage == QuestionStage.CLOSING_TONE and not ctx.tone:
        ctx.tone = text
    else:
        ctx.additional_notes.append(text)
    return ctx
