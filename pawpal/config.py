"""Environment-driven configuration. No secrets are hardcoded."""
import os

from dotenv import load_dotenv

load_dotenv()


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///pawpal.db")


def openai_api_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    return key or None


def openai_model() -> str:
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
