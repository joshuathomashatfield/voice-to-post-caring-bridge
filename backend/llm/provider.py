"""
LLM backend abstraction.

Any provider just needs to implement `generate(messages) -> str`. This keeps
post_generator.py, conversation.py, and intents.py completely decoupled from
which model or API is actually running underneath.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Dict

logger = logging.getLogger("caringbridge.llm")


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, messages: List[Dict[str, str]], temperature: float = 0.4) -> str:
        """messages: list of {"role": "system"|"user"|"assistant", "content": str}
        Returns the assistant's text response."""
        raise NotImplementedError


class LLMUnavailableError(RuntimeError):
    """Raised when no working LLM backend could be reached."""


class NullProvider(LLMProvider):
    """A safe, deterministic fallback used when no LLM backend is configured
    or reachable. It never invents facts -- it performs the simplest possible
    templated behavior so the rest of the app remains usable (e.g. for
    offline development, demos, or automated tests) without pretending to be
    a real language model."""

    def generate(self, messages: List[Dict[str, str]], temperature: float = 0.4) -> str:
        logger.info("null_provider_invoked")
        last_user = ""
        for m in reversed(messages):
            if m["role"] == "user":
                last_user = m["content"]
                break
        system = messages[0]["content"] if messages else ""

        # Very small amount of templated behavior so intent classification
        # and extraction still degrade gracefully rather than crashing.
        if "Classify the user's message into exactly one" in system:
            return '{"intent": "ANSWER", "confidence": 0.4, "instruction": null}'
        if "structured JSON" in system and "extract" in system.lower():
            return "{}"
        if "Using ONLY the user-provided facts" in system:
            return (
                "I don't have a language model configured right now, so I can't draft "
                "the post automatically. Please configure LLM_PROVIDER in your .env file, "
                "or write your update directly in the editable text area below."
            )
        if "Revise the CaringBridge post" in system:
            return last_user or ""
        return "I'm not able to respond right now -- please configure a language model backend."


def get_provider() -> LLMProvider:
    from backend.config import settings

    if settings.llm_provider == "ollama":
        from backend.llm.ollama_provider import OllamaProvider
        return OllamaProvider()
    if settings.llm_provider == "openai_compatible":
        from backend.llm.ollama_provider import OpenAICompatibleProvider
        return OpenAICompatibleProvider()
    logger.info("llm_provider_none configured=%s", settings.llm_provider)
    return NullProvider()
