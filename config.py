# config.py
import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    """App settings from the project-root `.env` file only (not from the shell environment)."""

    model_config = SettingsConfigDict(
        env_file=_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Drop env_settings so ANTHROPIC_API_KEY etc. are not read from os.environ.
        return init_settings, dotenv_settings, file_secret_settings

    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    message_fetch_limit: int = Field(default=50, validation_alias="MESSAGE_FETCH_LIMIT")
    lookback_minutes: int = Field(default=60, validation_alias="LOOKBACK_MINUTES")
    chat_db_path: str = Field(
        default_factory=lambda: os.path.expanduser("~/Library/Messages/chat.db"),
        validation_alias="CHAT_DB_PATH",
    )
    stop_reply_text: str = Field(default="STOP", validation_alias="STOP_REPLY_TEXT")
    log_file: str = Field(default="sms_agent.log", validation_alias="LOG_FILE")
    classify_max_workers: int = Field(
        default=8,
        ge=1,
        le=32,
        validation_alias="CLASSIFY_MAX_WORKERS",
    )

    @field_validator("chat_db_path", mode="before")
    @classmethod
    def expand_chat_path(cls, v: object) -> object:
        if isinstance(v, str):
            return os.path.expanduser(v)
        return v


settings = Settings()

# Module-level aliases (existing imports); `DRY_RUN` is toggled at runtime by `main.py`.
ANTHROPIC_API_KEY = settings.anthropic_api_key
MESSAGE_FETCH_LIMIT = settings.message_fetch_limit
LOOKBACK_MINUTES = settings.lookback_minutes
CHAT_DB_PATH = settings.chat_db_path
STOP_REPLY_TEXT = settings.stop_reply_text
LOG_FILE = settings.log_file
CLASSIFY_MAX_WORKERS = settings.classify_max_workers
DRY_RUN = False
# True while main.process_once runs with --quiet (redacted identifiers in action logs; minimal INFO).
QUIET = False
