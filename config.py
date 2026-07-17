from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

VALID_MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")
VALID_PROVIDERS = ("openai", "anthropic")


VALID_ENGINES = ("whisper", "gemini")


@dataclass
class Config:
    llm_provider: str = "anthropic"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    llm_model: str = "claude-sonnet-4-20250514"
    transcription_engine: str = "whisper"
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    sample_rate: int = 16000
    channels: int = 1
    output_dir: Path = field(default_factory=lambda: Path("recordings"))

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> Config:
        load_dotenv(env_path or ".env", override=True)
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "anthropic").lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "claude-sonnet-4-20250514"),
            transcription_engine=os.getenv("TRANSCRIPTION_ENGINE", "whisper").lower(),
            whisper_model_size=os.getenv("WHISPER_MODEL_SIZE", "small"),
            whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
            whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        )

    def validate(self) -> list[str]:
        warnings: list[str] = []

        if self.llm_provider not in VALID_PROVIDERS:
            warnings.append(
                f"Unknown LLM provider '{self.llm_provider}'. "
                f"Valid: {', '.join(VALID_PROVIDERS)}"
            )

        if self.whisper_model_size not in VALID_MODEL_SIZES:
            warnings.append(
                f"Unknown Whisper model '{self.whisper_model_size}'. "
                f"Valid: {', '.join(VALID_MODEL_SIZES)}"
            )

        if self.transcription_engine not in VALID_ENGINES:
            warnings.append(
                f"Unknown transcription engine '{self.transcription_engine}'. "
                f"Valid: {', '.join(VALID_ENGINES)}"
            )

        if self.transcription_engine == "gemini" and not self.gemini_api_key:
            warnings.append("GEMINI_API_KEY is not set — Gemini transcription unavailable")

        if self.llm_provider == "openai" and not self.openai_api_key:
            warnings.append("OPENAI_API_KEY is not set — summarization will be skipped")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            warnings.append("ANTHROPIC_API_KEY is not set — summarization will be skipped")

        return warnings

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.gemini_api_key and self.gemini_api_key != "...")

    @property
    def has_api_key(self) -> bool:
        if self.llm_provider == "openai":
            return bool(self.openai_api_key and self.openai_api_key != "sk-...")
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key and self.anthropic_api_key != "sk-ant-...")
        return False
