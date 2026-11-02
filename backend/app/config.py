from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Application settings loaded from the repository-root .env file."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"



    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'hr.db'}"
    session_secret: str = "dev-only-change-me"
    transcriber: Literal["whisper", "mock"] = "whisper"
    upload_dir: str = str(REPO_ROOT / "data" / "uploads")

    hr_username: str = "hr"            # MOCK sign-in credentials, demo only
    hr_password: str = "hr-demo"


@lru_cache
def get_settings() -> Settings:
    return Settings()
