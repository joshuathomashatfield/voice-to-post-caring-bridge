"""
Concrete LLM providers: local Ollama and any OpenAI-compatible HTTP API.

Both fall back to backend.llm.provider.NullProvider behavior (raising
LLMUnavailableError upward, which callers catch) rather than crashing the
request if the backend is unreachable -- a network hiccup while drafting a
first CaringBridge post should never destroy the user's answers.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import httpx

from backend.config import settings
from backend.llm.provider import LLMProvider, LLMUnavailableError

logger = logging.getLogger("caringbridge.llm")


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        self.host = settings.ollama_host.rstrip("/")
        self.model = settings.llm_model
        self.timeout = settings.llm_request_timeout

    def generate(self, messages: List[Dict[str, str]], temperature: float = 0.4) -> str:
        url = f"{self.host}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("ollama_request_failed error=%s", exc)
            raise LLMUnavailableError(str(exc)) from exc


class OpenAICompatibleProvider(LLMProvider):
    """Works with the OpenAI API itself or any OpenAI-compatible endpoint
    (LM Studio, vLLM's OpenAI shim, OpenRouter, etc.)."""

    def __init__(self) -> None:
        self.base_url = settings.openai_compatible_base_url.rstrip("/")
        self.api_key = settings.openai_compatible_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_request_timeout

    def generate(self, messages: List[Dict[str, str]], temperature: float = 0.4) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("openai_compatible_request_failed error=%s", exc)
            raise LLMUnavailableError(str(exc)) from exc
