"""
FastAPI application entrypoint for the CaringBridge First-Post Voice Assistant.

Run with:
    python app.py
or:
    uvicorn app:app --reload

See README.md for full setup instructions.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import conversation, post_generator, privacy
from backend.config import settings
from backend.intents import classify_intent, is_actionable
from backend.llm.provider import LLMUnavailableError, get_provider
from backend.models import ConversationTurn, Intent, PostContext, QuestionStage, SessionState
from backend.state import session_manager
from backend.stt.audio_utils import (
    AudioValidationError, cleanup_temp_files, normalize_to_wav, validate_upload,
)
from backend.stt.whisper_engine import transcribe as whisper_transcribe

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("caringbridge.app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

llm_provider = get_provider()


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #

class NewSessionResponse(BaseModel):
    session_id: str
    welcome_message: str
    first_question: Optional[str]


class MessageRequest(BaseModel):
    session_id: str
    text: str
    via_voice: bool = False


class TurnResponse(BaseModel):
    session_id: str
    intent: str
    confidence: float
    assistant_message: Optional[str] = None
    next_question: Optional[str] = None
    stage: str
    post_text: Optional[str] = None
    privacy_flags: List[str] = []
    ready_to_draft: bool = False
    speak: Optional[str] = None  # text the frontend should optionally read aloud
    error: Optional[str] = None


class ReviseRequest(BaseModel):
    session_id: str
    instruction: str


class ManualEditRequest(BaseModel):
    session_id: str
    text: str


class ExportRequest(BaseModel):
    session_id: str
    format: str = "txt"  # txt | md


class SimpleSessionRequest(BaseModel):
    session_id: str


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _get_session(session_id: str) -> SessionState:
    try:
        return session_manager.require(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown or expired session_id.")


def _current_privacy_flags(session: SessionState) -> List[str]:
    text = session.current_post_text or ""
    return [f"{flag.message} ({flag.matched_text})" for flag in privacy.scan_text(text)]


def _generate_and_store(session: SessionState) -> str:
    draft = post_generator.generate_draft(session.context, session.current_post_text or "", llm_provider)
    session_manager.add_version(session, draft, source="generated")
    return draft


def _revise_and_store(session: SessionState, instruction: str) -> str:
    current = session.current_post_text
    if not current:
        # No post yet: treat the "revision" instruction as informative content
        # for the next draft rather than failing.
        session.context.additional_notes.append(instruction)
        return _generate_and_store(session)
    revised = post_generator.revise_post(current, instruction, session.context, llm_provider)
    session_manager.add_version(session, revised, source="revised")
    session.ai_revisions += 1
    return revised


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #

@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")


# --------------------------------------------------------------------------- #
# Session lifecycle
# --------------------------------------------------------------------------- #

@app.post("/api/session", response_model=NewSessionResponse)
def create_session():
    session = session_manager.create()
    question = conversation.next_question(session)
    session.conversation_history.append(
        ConversationTurn(role="assistant", content=conversation.WELCOME_MESSAGE)
    )
    if question:
        session.conversation_history.append(ConversationTurn(role="assistant", content=question))
    session_manager.save(session)
    return NewSessionResponse(
        session_id=session.session_id,
        welcome_message=conversation.WELCOME_MESSAGE,
        first_question=question,
    )


@app.post("/api/clear")
def clear_session(req: SimpleSessionRequest):
    session_manager.clear(req.session_id)
    return {"status": "cleared"}


@app.get("/api/state/{session_id}")
def get_state(session_id: str):
    session = _get_session(session_id)
    return {
        "session_id": session.session_id,
        "stage": session.stage.value,
        "context": session.context.model_dump(),
        "conversation_history": [t.model_dump() for t in session.conversation_history],
        "post_text": session.current_post_text,
        "post_versions": len(session.post_versions),
        "can_undo": session.current_version_index > 0,
        "can_redo": session.current_version_index < len(session.post_versions) - 1,
    }


# --------------------------------------------------------------------------- #
# Core turn processing (shared by typed + transcribed voice input)
# --------------------------------------------------------------------------- #

def _process_turn(session: SessionState, text: str, via_voice: bool) -> TurnResponse:
    session.conversation_history.append(ConversationTurn(role="user", content=text, via_voice=via_voice))
    if via_voice:
        session.voice_turns += 1
    else:
        session.typed_turns += 1

    result = classify_intent(text, llm_provider)
    if not is_actionable(result):
        result.intent = Intent.ANSWER

    response = TurnResponse(
        session_id=session.session_id,
        intent=result.intent.value,
        confidence=result.confidence,
        stage=session.stage.value,
    )

    try:
        if result.intent == Intent.ANSWER:
            before = session.context.model_copy(deep=True)
            conversation.extract_and_merge(session, text, llm_provider)
            if session.context != before:
                session.meaningful_answers += 1
            question = conversation.next_question(session)
            response.next_question = question
            response.ready_to_draft = question is None and session.context.has_minimum_content()
            if question:
                session.conversation_history.append(ConversationTurn(role="assistant", content=question))
            elif response.ready_to_draft:
                msg = ("Thank you -- I think I have enough to put together a first draft. "
                       "Say \"write the post\" whenever you're ready, or keep telling me more.")
                response.assistant_message = msg
                session.conversation_history.append(ConversationTurn(role="assistant", content=msg))

        elif result.intent == Intent.SKIP:
            conversation.advance_after_answer(session)
            question = conversation.next_question(session)
            response.next_question = question
            if question:
                session.conversation_history.append(ConversationTurn(role="assistant", content=question))

        elif result.intent == Intent.GO_BACK:
            conversation.go_back(session)
            question = conversation.next_question(session)
            response.next_question = question

        elif result.intent == Intent.RESTART:
            fresh = SessionState(session_id=session.session_id)
            session_manager.save(fresh)
            session = fresh
            question = conversation.next_question(session)
            response.assistant_message = "No problem -- let's start fresh."
            response.next_question = question
            response.stage = session.stage.value

        elif result.intent == Intent.GENERATE_POST:
            draft = _generate_and_store(session)
            response.post_text = draft
            response.assistant_message = "Here's a first draft based on what you've shared. Feel free to edit it directly, or ask me to change anything."
            response.privacy_flags = _current_privacy_flags(session)

        elif result.intent == Intent.REVISE_POST:
            instruction = result.instruction or text
            revised = _revise_and_store(session, instruction)
            response.post_text = revised
            response.assistant_message = "I've updated the post."
            response.privacy_flags = _current_privacy_flags(session)

        elif result.intent == Intent.DELETE_INFORMATION:
            instruction = result.instruction or text
            session.context.privacy_constraints.append(instruction)
            revised = _revise_and_store(session, f"Remove the following as requested: {instruction}")
            response.post_text = revised
            response.assistant_message = "Done -- I've removed that."
            response.privacy_flags = _current_privacy_flags(session)

        elif result.intent == Intent.PRIVACY_REQUEST:
            session.context.privacy_constraints.append(text)
            response.assistant_message = "Understood -- I'll keep that in mind and won't include it."

        elif result.intent == Intent.READ_POST:
            if session.current_post_text:
                response.speak = session.current_post_text
                session.used_tts = True
            else:
                response.assistant_message = "There isn't a post to read yet -- would you like me to draft one?"

        elif result.intent == Intent.STOP_AUDIO:
            response.assistant_message = None  # frontend stops playback locally

        elif result.intent == Intent.UNDO:
            version = session_manager.undo(session)
            response.post_text = version.text if version else session.current_post_text
            if version is None:
                response.assistant_message = "There's nothing earlier to undo to."

        elif result.intent == Intent.REDO:
            version = session_manager.redo(session)
            response.post_text = version.text if version else session.current_post_text
            if version is None:
                response.assistant_message = "There's nothing to redo."

        elif result.intent in (Intent.SAVE, Intent.COPY):
            response.post_text = session.current_post_text
            response.assistant_message = (
                "Use the Save or Copy buttons to export your post." if not session.current_post_text
                else "Your post is ready to save or copy."
            )

        else:  # UNKNOWN
            response.assistant_message = "I didn't quite follow that -- could you say it a different way?"

    except LLMUnavailableError:
        response.error = ("I couldn't reach the language model just now. Your answers are safely saved -- "
                           "please try again in a moment, or check your LLM_PROVIDER configuration.")

    session_manager.save(session)
    response.stage = session.stage.value
    return response


@app.post("/api/message", response_model=TurnResponse)
def send_message(req: MessageRequest):
    session = _get_session(req.session_id)
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="Message text is empty.")
    return _process_turn(session, req.text.strip(), via_voice=req.via_voice)


# --------------------------------------------------------------------------- #
# Voice input
# --------------------------------------------------------------------------- #

class AudioTurnResponse(TurnResponse):
    raw_transcript: str = ""
    clean_transcript: str = ""
    transcript_reliable: bool = True


@app.post("/api/audio", response_model=AudioTurnResponse)
async def send_audio(session_id: str = Form(...), file: UploadFile = File(...)):
    session = _get_session(session_id)
    raw_bytes = await file.read()

    try:
        validate_upload(raw_bytes, file.content_type or "")
    except AudioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    wav_path = None
    try:
        wav_path = normalize_to_wav(raw_bytes, suffix=suffix)
        transcript = whisper_transcribe(wav_path)
    except RuntimeError as exc:
        logger.error("audio_processing_failed error=%s", exc)
        return AudioTurnResponse(
            session_id=session.session_id,
            intent=Intent.UNKNOWN.value,
            confidence=0.0,
            stage=session.stage.value,
            error="I couldn't transcribe that recording. Your previous responses are still safe.",
            raw_transcript="",
            clean_transcript="",
            transcript_reliable=False,
        )
    finally:
        if wav_path:
            cleanup_temp_files(wav_path)

    if not transcript.is_reliable:
        session.stt_retry_attempts += 1
        session_manager.save(session)
        return AudioTurnResponse(
            session_id=session.session_id,
            intent=Intent.UNKNOWN.value,
            confidence=0.0,
            stage=session.stage.value,
            assistant_message="I didn't quite catch that. Could you say it one more time?",
            raw_transcript=transcript.raw_transcript,
            clean_transcript=transcript.clean_transcript,
            transcript_reliable=False,
        )

    base_response = _process_turn(session, transcript.clean_transcript, via_voice=True)
    return AudioTurnResponse(
        **base_response.model_dump(),
        raw_transcript=transcript.raw_transcript,
        clean_transcript=transcript.clean_transcript,
        transcript_reliable=True,
    )


# --------------------------------------------------------------------------- #
# Post editing / generation / revision / undo-redo
# --------------------------------------------------------------------------- #

@app.post("/api/generate")
def generate(req: SimpleSessionRequest):
    session = _get_session(req.session_id)
    try:
        draft = _generate_and_store(session)
    except LLMUnavailableError:
        raise HTTPException(status_code=502, detail="Couldn't generate the draft just now. Your responses have been preserved.")
    session_manager.save(session)
    return {"post_text": draft, "privacy_flags": _current_privacy_flags(session)}


@app.post("/api/revise")
def revise(req: ReviseRequest):
    session = _get_session(req.session_id)
    try:
        revised = _revise_and_store(session, req.instruction)
    except LLMUnavailableError:
        raise HTTPException(status_code=502, detail="Couldn't generate the revision just now. Your previous draft is still safe.")
    session_manager.save(session)
    return {"post_text": revised, "privacy_flags": _current_privacy_flags(session)}


@app.post("/api/manual-edit")
def manual_edit(req: ManualEditRequest):
    """Records a manual edit as a new version so the user's own wording is
    preserved and later revisions build on it, not on a stale AI draft."""
    session = _get_session(req.session_id)
    session_manager.add_version(session, req.text, source="manual_edit")
    session.manual_edits += 1
    session_manager.save(session)
    return {"post_text": req.text, "privacy_flags": _current_privacy_flags(session)}


@app.post("/api/undo")
def undo(req: SimpleSessionRequest):
    session = _get_session(req.session_id)
    version = session_manager.undo(session)
    session_manager.save(session)
    return {"post_text": version.text if version else session.current_post_text}


@app.post("/api/redo")
def redo(req: SimpleSessionRequest):
    session = _get_session(req.session_id)
    version = session_manager.redo(session)
    session_manager.save(session)
    return {"post_text": version.text if version else session.current_post_text}


@app.post("/api/export")
def export(req: ExportRequest):
    session = _get_session(req.session_id)
    text = session.current_post_text
    if not text:
        raise HTTPException(status_code=400, detail="There's no post to export yet.")
    ext = "md" if req.format == "md" else "txt"
    return PlainTextResponse(text, media_type=f"text/{'markdown' if ext == 'md' else 'plain'}",
                              headers={"Content-Disposition": f'attachment; filename="caringbridge_first_post.{ext}"'})


@app.get("/health")
def health():
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=settings.host, port=settings.port, reload=settings.debug)
