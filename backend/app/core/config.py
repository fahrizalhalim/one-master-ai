from pathlib import Path

# Muat .env backend secara manual (tanpa dependensi tambahan).
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


_env = load_env()

OPENAI_API_KEY = _env.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = _env.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = _env.get("OPENAI_MODEL", "gpt-4o-mini")
DATABASE_URL = _env.get("DATABASE_URL", "")

APP_NAME = "ONE MASTER AI"
APP_VERSION = "0.1.0"
