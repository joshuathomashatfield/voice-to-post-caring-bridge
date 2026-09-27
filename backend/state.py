"""Session-state helpers.

The hosted Vercel profile keeps the authoritative conversation state in the
participant's browser and sends it with each API request.  This module retains
a small in-memory manager for local development/tests and provides the version
history helpers used by both modes.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Dict, Optional

from backend.config import settings
from backend.models import PostVersion, SessionState

logger = logging.getLogger("caringbridge.state")


class SessionManager:
    """In-memory store plus draft-version helpers."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def create(self) -> SessionState:
        session = SessionState()
        self.save(session)
        logger.info("session_created session_id=%s", session.session_id)
        return session

    def get(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            return self._sessions.get(session_id)

    def require(self, session_id: str) -> SessionState:
        session = self.get(session_id)
        if session is None:
            raise KeyError(f"Unknown session_id: {session_id}")
        return session

    def save(self, session: SessionState) -> None:
        session.touch()
        with self._lock:
            self._sessions[session.session_id] = session

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.info("session_cleared session_id=%s", session_id)

    def sweep_expired(self) -> int:
        cutoff = time.time() - settings.session_ttl_minutes * 60
        removed = 0
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s.updated_at < cutoff]
            for sid in expired:
                del self._sessions[sid]
                removed += 1
        return removed

    @staticmethod
    def context_digest(session: SessionState) -> str:
        return hashlib.sha256(session.context.model_dump_json().encode()).hexdigest()

    @staticmethod
    def add_version(session: SessionState, text: str, source: str) -> PostVersion:
        next_version_no = max((v.version for v in session.post_versions), default=0) + 1
        session.post_versions = session.post_versions[: session.current_version_index + 1]
        version = PostVersion(
            version=next_version_no,
            text=text,
            source=source,
            context_digest=SessionManager.context_digest(session),
        )
        session.post_versions.append(version)
        session.current_version_index = len(session.post_versions) - 1
        return version

    @staticmethod
    def undo(session: SessionState) -> Optional[PostVersion]:
        if session.current_version_index > 0:
            session.current_version_index -= 1
            session.undo_actions += 1
            return session.post_versions[session.current_version_index]
        return None

    @staticmethod
    def redo(session: SessionState) -> Optional[PostVersion]:
        if session.current_version_index < len(session.post_versions) - 1:
            session.current_version_index += 1
            return session.post_versions[session.current_version_index]
        return None


session_manager = SessionManager()
