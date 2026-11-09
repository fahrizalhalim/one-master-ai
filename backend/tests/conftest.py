"""
Pengaman global: seluruh pytest wajib menggunakan SQLite sementara.
Jangan pernah menggunakan DATABASE_URL dari Docker Compose/produksi.
"""
import os
import tempfile
from pathlib import Path

_TEST_DIR = Path(tempfile.mkdtemp(prefix="one-master-ai-pytest-"))
_TEST_DB = _TEST_DIR / "pytest.db"

# Paksa override, bukan setdefault.
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")

if not os.environ["DATABASE_URL"].startswith("sqlite:///"):
    raise RuntimeError(
        "BAHAYA: pytest dibatalkan karena DATABASE_URL bukan SQLite."
    )
