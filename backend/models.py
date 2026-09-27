"""
Pydantic data models shared across the backend.

Keeping these in one module (rather than scattering dicts / dataclasses)
gives the LLM structured-extraction layer something concrete to validate
against, and gives the rest of the app one source of truth for shape.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Intent model
# --------------------------------------------------------------------------- #

class Intent(str, Enum):
    ANSWER = "ANSWER"
    SKIP = "SKIP"
    GO_BACK = "GO_BACK"
    RESTART = "RESTART"
    GENERATE_POST = "GENERATE_POST"
    REVISE_POST = "REVISE_POST"
    READ_POST = "READ_POST"
    STOP_AUDIO = "STOP_AUDIO"
    UNDO = "UNDO"
    REDO = "REDO"
    SAVE = "SAVE"
    COPY = "COPY"
    DELETE_INFORMATION = "DELETE_INFORMATION"
    PRIVACY_REQUEST = "PRIVACY_REQUEST"
    UNKNOWN = "UNKNOWN"


class IntentResult(BaseModel):
    intent: Intent = Intent.UNKNOWN
    confidence: float = 0.0
    instruction: Optional[str] = None  # free-text instruction, e.g. for REVISE_POST


# --------------------------------------------------------------------------- #
# Conversation state
# --------------------------------------------------------------------------- #

class ConversationTurn(BaseModel):
    role: str  # "assistant" | "user"
    content: str
    timestamp: float = Field(default_factory=time.time)
    via_voice: bool = False


class PostContext(BaseModel):
    """Structured information gathered from the user. Nothing here is ever
    invented by the assistant -- every field is populated only from what the
    user explicitly said."""

    person: Optional[str] = None
    relationship: Optional[str] = None
    reason_for_page: Optional[str] = None
    current_situation: Optional[str] = None
    current_status: Optional[str] = None
    important_updates: List[str] = Field(default_factory=list)
    support_requests: List[str] = Field(default_factory=list)
    communication_preferences: Optional[str] = None
    tone: Optional[str] = None
    privacy_constraints: List[str] = Field(default_factory=list)
    additional_notes: List[str] = Field(default_factory=list)

    def has_minimum_content(self) -> bool:
        """Whether we plausibly have enough to draft an initial post."""
        filled = [
            self.reason_for_page,
            self.current_situation,
            self.current_status,
        ]
        meaningful = [f for f in filled if f]
        return len(meaningful) >= 2 or len(self.important_updates) >= 2


class ExtractedInfo(BaseModel):
    """Shape the LLM must return when extracting information from a user
    utterance. Validated before being merged into PostContext."""

    acknowledgement: Optional[str] = None
    person: Optional[str] = None
    relationship: Optional[str] = None
    reason_for_page: Optional[str] = None
    current_situation: Optional[str] = None
    current_status: Optional[str] = None
    important_updates: List[str] = Field(default_factory=list)
    support_requests: List[str] = Field(default_factory=list)
    communication_preferences: Optional[str] = None
    tone: Optional[str] = None
    privacy_constraints: List[str] = Field(default_factory=list)
    additional_notes: List[str] = Field(default_factory=list)


class PostVersion(BaseModel):
    context_digest: str = ""
    version: int
    text: str
    timestamp: float = Field(default_factory=time.time)
    source: str = "generated"  # "generated" | "revised" | "manual_edit"


class QuestionStage(str, Enum):
    OPENING = "OPENING"
    CURRENT_SITUATION = "CURRENT_SITUATION"
    HOW_DOING = "HOW_DOING"
    SUPPORT = "SUPPORT"
    COMMUNICATION = "COMMUNICATION"
    CLOSING_TONE = "CLOSING_TONE"
    READY_TO_DRAFT = "READY_TO_DRAFT"
    DONE = "DONE"


class TranscriptResult(BaseModel):
    raw_transcript: str
    clean_transcript: str
    confidence: float = 1.0
    is_reliable: bool = True
    no_speech_probability: float = 0.0
    duration_seconds: float = 0.0


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)

    stage: QuestionStage = QuestionStage.OPENING
    stage_history: List[QuestionStage] = Field(default_factory=list)

    context: PostContext = Field(default_factory=PostContext)
    conversation_history: List[ConversationTurn] = Field(default_factory=list)

    post_versions: List[PostVersion] = Field(default_factory=list)
    current_version_index: int = -1  # index into post_versions, -1 = none yet

    meaningful_answers: int = 0
    stt_retry_attempts: int = 0
    voice_turns: int = 0
    typed_turns: int = 0
    ai_revisions: int = 0
    manual_edits: int = 0
    undo_actions: int = 0
    used_tts: bool = False
    context_corrections: dict = Field(default_factory=dict)
    last_acknowledgement: Optional[str] = None
    draft_needs_update: bool = False

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def current_post_text(self) -> Optional[str]:
        if 0 <= self.current_version_index < len(self.post_versions):
            return self.post_versions[self.current_version_index].text
        return None
