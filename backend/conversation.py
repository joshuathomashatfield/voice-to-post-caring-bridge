"""
Conversation policy: which question to ask next, and how to turn a user's
free-text answer into structured PostContext fields.

The question SEQUENCE is fixed and small (this is a conversation, not a
survey), but any stage is skipped automatically once enough information for
it already exists -- see `next_question`. The LLM extracts
structured facts and a short spoken acknowledgement from what the user said; it is never allowed to
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
        "What's been happening recently?"
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
{"acknowledgement": str|null, "person": str|null, "relationship": str|null, "reason_for_page": str|null,
"current_situation": str|null, "current_status": str|null,
"important_updates": [str], "support_requests": [str],
"communication_preferences": str|null, "tone": str|null,
"privacy_constraints": [str], "additional_notes": [str]}

Only fill fields that are actually supported by the user's message. Do not
diagnose medical conditions, guess prognosis, or assume religious/emotional
content. If the user asks to omit or not mention something, put a short
description of it in "privacy_constraints".

Also write an acknowledgement to speak directly to the user: one brief,
natural sentence (at most 25 words) responding to what they just told you.
Use everyday language and contractions. Don't ask a question; the app supplies
the next question. Don't repeat sensitive names or medical details. Don't assume
feelings or promise outcomes. Avoid stock praise, forced optimism, "noted", and
claims that you have already changed the draft. If they ask what a question
means, briefly explain it without treating that question as a fact.
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

    if session.stage != stage:
        session.stage_history.append(session.stage)
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
    session.last_acknowledgement = None
    try:
        raw = llm.generate([
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Current question stage: {session.stage.value}\n"
                f"Recent conversation: {json.dumps([t.model_dump(include={'role', 'content'}) for t in session.conversation_history[-5:]])}\n"
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
        # Keep uncertain extraction visibly unorganized; don't silently turn
        # a clarification or a failed model response into a medical fact.
        if user_text.strip() not in session.context.additional_notes:
            session.context.additional_notes.append(user_text.strip())
        session.last_acknowledgement = "I've kept your words in the notes so you can check them."
        return session.context, False

    ctx = session.context
    acknowledgement = (extracted.acknowledgement or "").strip()
    if acknowledgement and len(acknowledgement.split()) <= 40 and "?" not in acknowledgement:
        session.last_acknowledgement = acknowledgement
    for field in ("person", "relationship", "reason_for_page", "current_situation",
                  "current_status", "communication_preferences", "tone"):
        value = getattr(extracted, field)
        if value:
            setattr(ctx, field, value)
    for field in ("important_updates", "support_requests", "privacy_constraints", "additional_notes"):
        target = getattr(ctx, field)
        target.extend(value for value in getattr(extracted, field) if value not in target)

    return ctx, True


def question_for_stage(stage: QuestionStage) -> Optional[str]:
    """Repeat a previous question without skipping its already-filled field."""
    return _QUESTIONS.get(stage)
