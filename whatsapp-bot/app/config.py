from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # From Meta for Developers > your app > WhatsApp > API Setup
    whatsapp_token: str
    whatsapp_phone_number_id: str
    # Any string you choose; enter the same value in the webhook config on Meta
    whatsapp_verify_token: str
    # App Settings > Basic > App Secret. Used to check that webhook calls really come from Meta.
    whatsapp_app_secret: str = ""

    graph_api_version: str = "v23.0"
    flows_file: Path = BASE_DIR / "flows.yaml"
    # How long a user stays in a sub-menu / human hand-off before the bot resets them
    session_ttl_minutes: int = 30
    handoff_minutes: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
