"""
Lightweight conversational intent layer.

Deterministic rules handle the obvious commands cheaply and reliably
("skip", "start over", "read it back", ...). Anything that doesn't match a
rule is handed to the configured LLM provider for structured classification.
Low-confidence results are never allowed to trigger destructive actions
(RESTART, UNDO, DELETE_INFORMATION) -- callers should check `confidence`
before acting, or fall back to asking the user to confirm.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.llm.provider import LLMProvider
from backend.models import Intent, IntentResult

logger = logging.getLogger("caringbridge.intents")

# Ordered rule list: (compiled pattern, intent, confidence)
# Ordered rule list: (compiled pattern, intent, confidence)
_RULES = [
    (
        re.compile(
            r"^\s*(skip|pass|next question|i(?:'d)? rather not)\s*$",
            re.I,
        ),
        Intent.SKIP,
        0.95,
    ),
    (
        re.compile(
            r"^\s*(i don'?t know|not sure|no idea)[.!?]?\s*$",
            re.I,
        ),
        Intent.SKIP,
        0.7,
    ),
    (
        re.compile(
            r"\bgo back\b|\bprevious question\b|\bback up\b",
            re.I,
        ),
        Intent.GO_BACK,
        0.9,
    ),
    (
        re.compile(
            r"\bstart over\b|\brestart\b|\bstart again\b|\bclear everything\b",
            re.I,
        ),
        Intent.RESTART,
        0.95,
    ),

    # Only generate when the user clearly asks for a draft.
    (
        re.compile(
            r"^\s*(please\s+)?"
            r"(write|generate|create|draft)\s+"
            r"(the\s+|my\s+|a\s+)?"
            r"(post|draft)"
            r"(?:\s+(now|please|for me))?"
            r"[.!]?\s*$",
            re.I,
        ),
        Intent.GENERATE_POST,
        0.95,
    ),
    (
        re.compile(
            r"^\s*"
            r"(that'?s enough|"
            r"i'?m ready(?:\s+for\s+(?:the\s+)?draft)?|"
            r"ready to (?:write|draft)|"
            r"i'?m done answering)"
            r"[.!]?\s*$",
            re.I,
        ),
        Intent.GENERATE_POST,
        0.9,
    ),

    (
        re.compile(
            r"\bread\s+(it|the post|that)\s*(back|aloud)?\b|\blisten\b",
            re.I,
        ),
        Intent.READ_POST,
        0.85,
    ),
    (
        re.compile(
            r"\bstop\b.*\b(talking|reading|audio|speaking)\b|"
            r"\bstop audio\b|\bpause\b",
            re.I,
        ),
        Intent.STOP_AUDIO,
        0.9,
    ),
    (
        re.compile(r"\bundo\b", re.I),
        Intent.UNDO,
        0.9,
    ),
    (
        re.compile(r"\bredo\b", re.I),
        Intent.REDO,
        0.9,
    ),
    (
        re.compile(r"\bsave\b|\bexport\b|\bdownload\b", re.I),
        Intent.SAVE,
        0.85,
    ),
    (
        re.compile(r"\bcopy\b", re.I),
        Intent.COPY,
        0.85,
    ),
    (
        re.compile(
            r"\bremove\b.*\b(name|hospital|address|detail|mention)\b|"
            r"\bdon'?t (include|mention|say)\b|"
            r"\bdelete that\b|"
            r"\btake that out\b",
            re.I,
        ),
        Intent.DELETE_INFORMATION,
        0.8,
    ),
    (
        re.compile(
            r"\bmake\s+(it|that|the post|the first paragraph|the ending)\b.*\b"
            r"(shorter|longer|warmer|more private|less emotional|more upbeat|"
            r"more formal|less dramatic|briefer|simpler)\b",
            re.I,
        ),
        Intent.REVISE_POST,
        0.85,
    ),
    (
        re.compile(
            r"\brewrite\b|\brevise\b|\badd that\b|\bmake it more private\b",
            re.I,
        ),
        Intent.REVISE_POST,
        0.75,
    ),
    (
        re.compile(
            r"\b(private|privacy|don'?t share|keep.*confidential)\b",
            re.I,
        ),
        Intent.PRIVACY_REQUEST,
        0.6,
    ),
]

_EXTRACTION_SYSTEM_PROMPT = """You are an intent classifier for a supportive writing assistant.

Classify the user's message into exactly one of these intents:
ANSWER, SKIP, GO_BACK, RESTART, GENERATE_POST, REVISE_POST, READ_POST, STOP_AUDIO,
UNDO, REDO, SAVE, COPY, DELETE_INFORMATION, PRIVACY_REQUEST, UNKNOWN.

Respond ONLY with minified JSON:
{"intent":"<ONE_OF_THE_ABOVE>","confidence":<0.0-1.0>,"instruction":<string|null>}

IMPORTANT:

ANSWER means the user is sharing information, answering a question, describing
their situation, or explaining what they want the CaringBridge page to be about.

Do NOT classify a message as GENERATE_POST merely because the user uses words
such as "post", "draft", "page", "write", or "making a post" while describing
their situation.

GENERATE_POST requires a clear request for the assistant to produce the draft
now, such as:
- "Write the post."
- "Create the draft now."
- "I'm ready for the draft."
- "That's enough, go ahead and write it."

Examples:

"I'm making this post about my mother who recently had a stroke."
=> ANSWER

"I want this post to let everyone know how recovery is going."
=> ANSWER

"My mother had a stroke and things are going well."
=> ANSWER

"Okay, write the post now."
=> GENERATE_POST

If the user provides substantive personal/contextual information together with
ambiguous wording about making a post, prefer ANSWER unless they clearly request
generation now.

If the message asks for a change to an existing post, use REVISE_POST or
DELETE_INFORMATION and place the requested change in "instruction".
"""


def _rule_based(text: str) -> Optional[IntentResult]:
    stripped = text.strip()
    if not stripped:
        return None
    for pattern, intent, confidence in _RULES:
        if pattern.search(stripped):
            instruction = stripped if intent in (Intent.REVISE_POST, Intent.DELETE_INFORMATION) else None
            return IntentResult(intent=intent, confidence=confidence, instruction=instruction)
    return None


def classify_intent(text: str, llm: LLMProvider) -> IntentResult:
    """Classify a user utterance. Deterministic rules are tried first since
    they're cheap and precise; the LLM is only consulted for ambiguous
    natural language."""
    rule_hit = _rule_based(text)
    if rule_hit is not None:
        return rule_hit

    try:
        raw = llm.generate([
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ], temperature=0.0)
        cleaned = raw.strip().strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        data = json.loads(cleaned)
        intent_value = data.get("intent", "ANSWER")
        if intent_value not in Intent.__members__:
            intent_value = "UNKNOWN"
        return IntentResult(
            intent=Intent(intent_value),
            confidence=float(data.get("confidence", 0.5)),
            instruction=data.get("instruction"),
        )
    except Exception as exc:  # noqa: BLE001 -- classification must never crash the turn
        logger.warning("intent_classification_failed error=%s", exc)
        # Default to ANSWER at low confidence rather than guessing a
        # destructive intent -- this is the safest fallback.
        return IntentResult(intent=Intent.ANSWER, confidence=0.3, instruction=None)


def is_actionable(result: IntentResult, min_confidence: float = 0.55) -> bool:
    """Guard used by callers before executing a non-ANSWER intent. Destructive
    or state-changing intents require a higher bar than simple ones."""
    destructive = {Intent.RESTART, Intent.UNDO, Intent.REDO, Intent.DELETE_INFORMATION}
    threshold = 0.7 if result.intent in destructive else min_confidence
    return result.confidence >= threshold
