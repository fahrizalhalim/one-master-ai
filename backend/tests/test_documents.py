"""Tes generator dokumen DOCX/PDF."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_doc.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User
from app.services.document_service import parse_blocks  # noqa: E402

client = TestClient(app)

EMAIL = "doc@test.ac.id"
PASSWORD = "password123"
SAMPLE_MD = """# Rencana Pembelajaran

## Tujuan
- Mahasiswa mampu berpikir logis
- Mahasiswa mampu mengimplementasi

## Langkah
1. Analisis masalah
2. Desain solusi

Paragraf penutup dengan **teks bold** dan `kode`.
"""


def setup_function():
    Base.metadata.create_all(bind=engine)


def _approve_all():
    """Setujui semua user pending langsung via DB (test helper)."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.status == "pending").update(
            {"status": "active"}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _token() -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD},
    )
    if r.status_code != 201:
        r = client.post(
            "/api/v1/auth/login",
            data={"username": EMAIL, "password": PASSWORD},
        )
    _approve_all()
    return r.json()["access_token"]


def test_parse_blocks():
    blocks = parse_blocks(SAMPLE_MD)
    kinds = [b[0] for b in blocks]
    assert "title" in kinds and "bullet" in kinds and "number" in kinds


def test_generate_docx():
    t = _token()
    r = client.post(
        "/api/v1/documents/generate",
        json={"title": "RPS Algoritma", "content": SAMPLE_MD, "format": "docx"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filename"].endswith(".docx")

    # unduh
    r2 = client.get(
        f"/api/v1/documents/download/{data['filename']}",
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r2.status_code == 200
    # DOCX dimulai dengan signature PK (zip)
    assert r2.content[:2] == b"PK"


def test_generate_pdf():
    t = _token()
    r = client.post(
        "/api/v1/documents/generate",
        json={"title": "Laporan Uji", "content": SAMPLE_MD, "format": "pdf"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["filename"].endswith(".pdf")

    r2 = client.get(
        f"/api/v1/documents/download/{data['filename']}",
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r2.status_code == 200
    assert r2.content[:4] == b"%PDF"


def test_download_requires_token():
    r = client.get("/api/v1/documents/download/apa_aja.docx")
    assert r.status_code in (401, 403)


def test_download_path_traversal_blocked():
    t = _token()
    r = client.get(
        "/api/v1/documents/download/..%2F..%2F.env",
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 404


def test_invalid_format_rejected():
    t = _token()
    r = client.post(
        "/api/v1/documents/generate",
        json={"title": "X", "content": "isi", "format": "exe"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r.status_code == 422
