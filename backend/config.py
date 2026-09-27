"""
Centralized application configuration.

All values can be overridden with environment variables (see .env.example).
Nothing sensitive is hard-coded here; secrets are read from the environment
only, never written into source.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- General ---
    app_name: str = "CaringBridge First-Post Assistant"
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # --- STT ---
    stt_provider: str = "local"          # local | groq
    stt_model: str = "small.en"          # tiny.en | base.en | small.en | medium.en | large-v3
    stt_device: str = "cpu"              # cpu | cuda
    stt_compute_type: str = "int8"       # int8 for cpu, float16 for cuda is typical
    min_speech_seconds: float = 0.5
    trailing_silence_seconds: float = 1.2
    max_recording_seconds: int = 120
    stt_language: str = "en"
    stt_request_timeout: float = 60.0

    # --- Groq (hosted LLM + STT) ---
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_stt_model: str = "whisper-large-v3-turbo"

    # --- TTS ---
    enable_tts: bool = True
    tts_engine: str = "browser"          # browser | pyttsx3 | piper
    piper_voice_path: str = ""           # path to a piper .onnx voice model, if tts_engine=piper

    # --- LLM ---
    llm_provider: str = "ollama"         # ollama | groq | openai_compatible | none
    llm_model: str = "qwen3:8b"
    ollama_host: str = "http://localhost:11434"
    openai_compatible_base_url: str = "https://api.openai.com/v1"
    openai_compatible_api_key: str = ""
    llm_temperature: float = 0.4
    llm_request_timeout: float = 60.0

    # --- Session / privacy ---
    # "client" is Vercel-safe: the browser owns SessionState and sends it
    # with each request. "memory" is kept for local development/tests.
    session_backend: str = "client"       # client | memory
    enable_autosave: bool = True
    enable_research_mode: bool = False
    session_ttl_minutes: int = 240

    # --- Draft generation ---
    draft_target_min_words: int = 150
    draft_target_max_words: int = 350


settings = Settings()
