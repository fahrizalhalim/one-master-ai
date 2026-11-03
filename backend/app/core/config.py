import os
from pathlib import Path

# Muat .env dari file (lokal) ATAU environment variable (Docker container).
ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


def load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def _get(key: str, default: str = "") -> str:
    """Prioritas: environment variable (Docker) -> file .env (lokal) -> default."""
    return os.environ.get(key) or _env.get(key, default)


_env = load_env()

OPENAI_API_KEY = _get("OPENAI_API_KEY")
OPENAI_BASE_URL = _get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _get("OPENAI_MODEL", "gpt-4o-mini")
DATABASE_URL = _get(
    "DATABASE_URL",
    "postgresql+psycopg://onemaster:onemaster@db:5432/onemaster",
)
JWT_SECRET_KEY = _get(
    "JWT_SECRET_KEY",
    "ubah-ini-di-env-produksi-58f8b3c1",
)

APP_NAME = "ONE MASTER AI"
APP_VERSION = "0.1.0"
