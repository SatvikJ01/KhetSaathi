"""Runtime configuration, loaded from environment variables / backend/.env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM: one of "gemini", "openai", "anthropic"
    llm_provider: str = "gemini"
    llm_model: str = ""  # empty -> provider default (see agent/llm.py)
    google_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Live data sources that need keys
    data_gov_api_key: str = ""  # https://data.gov.in -> My Account -> API key
    sarvam_api_key: str = ""

    # Networking
    http_timeout_s: float = 20.0
    cache_dir: str = ".cache"

    cors_origins: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
