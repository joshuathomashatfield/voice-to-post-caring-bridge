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
_RULES = [
    (re.compile(r"^\s*(skip|pass|next question|i(?:'d)? rather not)\s*$", re.I), Intent.SKIP, 0.95),
    (re.compile(r"\b(i don'?t know|not sure|no idea)\b", re.I), Intent.SKIP, 0.7),
    (re.compile(r"\bgo back\b|\bprevious question\b|\bback up\b", re.I), Intent.GO_BACK, 0.9),
    (re.compile(r"\bstart over\b|\brestart\b|\bstart again\b|\bclear everything\b", re.I), Intent.RESTART, 0.95),
    (re.compile(r"\b(write|generate|create|draft)\s+(the|my|a)?\s*post\b", re.I), Intent.GENERATE_POST, 0.9),
    (re.compile(r"\bthat'?s enough\b|\bready to (write|draft)\b|\bi'?m done answering\b", re.I),
     Intent.GENERATE_POST, 0.85),
    (re.compile(r"\bread\s+(it|the post|that)\s*(back|aloud)?\b|\blisten\b", re.I), Intent.READ_POST, 0.85),
    (re.compile(r"\bstop\b.*\b(talking|reading|audio|speaking)\b|\bstop audio\b|\bpause\b", re.I),
     Intent.STOP_AUDIO, 0.9),
    (re.compile(r"\bundo\b", re.I), Intent.UNDO, 0.9),
    (re.compile(r"\bredo\b", re.I), Intent.REDO, 0.9),
    (re.compile(r"\bsave\b|\bexport\b|\bdownload\b", re.I), Intent.SAVE, 0.85),
    (re.compile(r"\bcopy\b", re.I), Intent.COPY, 0.85),
    (re.compile(r"\bremove\b.*\b(name|hospital|address|detail|mention)\b|\bdon'?t (include|mention|say)\b|"
                r"\bdelete that\b|\btake that out\b", re.I), Intent.DELETE_INFORMATION, 0.8),
    (re.compile(r"\bmake\s+(it|that|the post|the first paragraph|the ending)\b.*\b"
                r"(shorter|longer|warmer|more private|less emotional|more upbeat|more formal|"
                r"less dramatic|briefer|simpler)\b", re.I), Intent.REVISE_POST, 0.85),
    (re.compile(r"\brewrite\b|\brevise\b|\badd that\b|\bmake it more private\b", re.I),
     Intent.REVISE_POST, 0.75),
    (re.compile(r"\b(private|privacy|don'?t share|keep.*confidential)\b", re.I), Intent.PRIVACY_REQUEST, 0.6),
]

_EXTRACTION_SYSTEM_PROMPT = """You are an intent classifier for a supportive writing assistant.
Classify the user's message into exactly one of these intents:
ANSWER, SKIP, GO_BACK, RESTART, GENERATE_POST, REVISE_POST, READ_POST, STOP_AUDIO,
UNDO, REDO, SAVE, COPY, DELETE_INFORMATION, PRIVACY_REQUEST, UNKNOWN.

Respond ONLY with minified JSON, no markdown fences, no commentary, matching:
{"intent": "<ONE_OF_THE_ABOVE>", "confidence": <0.0-1.0>, "instruction": "<optional free text instruction, or null>"}

If the message is simply answering a question conversationally (sharing information),
the intent is ANSWER. If it's asking for a change to a post ("make it shorter",
"remove my daughter's name"), the intent is REVISE_POST or DELETE_INFORMATION and
"instruction" should restate the requested change in a clear sentence.
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
