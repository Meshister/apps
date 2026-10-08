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

    # Phone numbers allowed to use the journal, with country code, comma separated (e.g. 972501234567).
    # Messages from anyone else are ignored. Empty = anyone can use it.
    owner_numbers: str = ""
    # Time zone for entry dates, e.g. Asia/Jerusalem, Europe/London, America/New_York
    timezone: str = "UTC"

    graph_api_version: str = "v23.0"
    journal_file: Path = BASE_DIR / "journal.yaml"
    database_file: Path = BASE_DIR / "data" / "journal.db"
    # An unfinished entry is discarded after this many hours without an answer
    draft_ttl_hours: int = 12

    @property
    def owners(self) -> set[str]:
        return {"".join(ch for ch in n if ch.isdigit()) for n in self.owner_numbers.split(",") if n.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
