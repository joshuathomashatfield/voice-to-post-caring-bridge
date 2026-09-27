"""
Draft generation and revision, using only user-supplied facts.

Both generate_draft() and revise_post() are strictly source-grounded: the
prompts explicitly forbid introducing facts that are not present in the
structured context / original post, matching MASTER PROMPT sections 35-36.
"""
from __future__ import annotations

import logging
import json

from backend.config import settings
from backend.llm.provider import LLMProvider, LLMUnavailableError
from backend.models import PostContext

logger = logging.getLogger("caringbridge.post_generator")

SYSTEM_PROMPT = """You are helping a person write their first CaringBridge update.

Your job is to help the user express information they choose to share with
friends and family. You are a writing assistant, not a medical professional.

Never invent facts.
Never diagnose, interpret medical symptoms, or predict medical outcomes.
Never assume the user's emotions, religious beliefs, prognosis, family
relationships, or preferences beyond what they stated.
Respect requests to omit or remove information.
When generating a draft, use only facts the user has explicitly provided.
Preserve the user's intended tone.
Write in natural, plain language.
Avoid excessive sentimentality, clichés, or generic inspirational language.
The user retains complete control over the final text.
Return only the requested post text -- no preamble, no explanation, no headers.
"""


def _format_context(context: PostContext) -> str:
    lines = []
    if context.person:
        lines.append(f"Who the update is about: {context.person}")
    if context.relationship:
        lines.append(f"Relationship of the author to that person: {context.relationship}")
    if context.reason_for_page:
        lines.append(f"Why this page was created: {context.reason_for_page}")
    if context.current_situation:
        lines.append(f"What has been happening: {context.current_situation}")
    if context.current_status:
        lines.append(f"How things are going currently: {context.current_status}")
    for item in context.important_updates:
        lines.append(f"Additional update: {item}")
    for item in context.support_requests:
        lines.append(f"Support the family would welcome: {item}")
    if context.communication_preferences:
        lines.append(f"Communication preference: {context.communication_preferences}")
    for item in context.additional_notes:
        lines.append(f"Other note (check meaning; may contain a question, not a fact): {item}")
    return "\n".join(f"- {line}" for line in lines) if lines else "(no facts provided yet)"


def _format_privacy(context: PostContext) -> str:
    if not context.privacy_constraints:
        return "(none specified)"
    return "\n".join(f"- Do not include: {c}" for c in context.privacy_constraints)


def generate_draft(context: PostContext, current_text: str, llm: LLMProvider, corrections: dict | None = None) -> str:
    prompt = f"""Using ONLY the user-provided facts below, write a first CaringBridge update.

FACTS:
{_format_context(context)}

USER PREFERENCES:
Tone: {context.tone or "not specified -- match the tone the user has used so far"}

CURRENT USER-WRITTEN TEXT IF ANY:
{current_text or "(none yet)"}

USER CORRECTIONS TO THE SHARED NOTES (override conflicting earlier draft details):
{json.dumps(corrections or {}, ensure_ascii=False)}
A corrected field with null or [] means the user removed its previous information.
Do not restore those removed details from the earlier draft. Preserve other manual wording.

PRIVACY CONSTRAINTS:
{_format_privacy(context)}

Requirements:
- Never invent details.
- Omit unknown information.
- Preserve requested privacy.
- Sound natural and personal.
- Use short readable paragraphs.
- Avoid medical advice.
- Avoid dramatic language unless the user used it.
- Avoid clichés.
- Target approximately {settings.draft_target_min_words}-{settings.draft_target_max_words} words unless the facts are too sparse for that length.

Return only the proposed post.
"""
    try:
        return llm.generate([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], temperature=settings.llm_temperature).strip()
    except LLMUnavailableError:
        logger.error("draft_generation_llm_unavailable")
        raise


def revise_post(current_post: str, revision_instruction: str, context: PostContext, llm: LLMProvider, corrections: dict | None = None) -> str:
    prompt = f"""Revise the CaringBridge post according to the user's instruction.

ORIGINAL POST:
{current_post}

USER INSTRUCTION:
{revision_instruction}

SOURCE FACTS:
{_format_context(context)}

USER CORRECTIONS TO THE SHARED NOTES (override conflicting earlier draft details):
{json.dumps(corrections or {}, ensure_ascii=False)}
A corrected field with null or [] means the user removed its previous information.
Do not restore those removed details from the earlier draft. Preserve other manual wording.

PRIVACY CONSTRAINTS:
{_format_privacy(context)}

Rules:
- Preserve factual accuracy.
- Only use facts in SOURCE FACTS, ORIGINAL POST, or explicitly supplied in USER INSTRUCTION.
- Never infer a medical fact from a request to change style.
- Follow removal requests exactly.
- Preserve manually entered information unless the user asks to remove it.
- Return only the revised post.
"""
    try:
        return llm.generate([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], temperature=settings.llm_temperature).strip()
    except LLMUnavailableError:
        logger.error("revision_llm_unavailable")
        raise
