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


def test_user_cannot_download_another_users_document():
    # User A membuat dokumen.
    token_a = _token()

    generated = client.post(
        "/api/v1/documents/generate",
        json={
            "title": "Dokumen Privat User A",
            "content": "Isi dokumen khusus User A.",
            "format": "docx",
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert generated.status_code == 200, generated.text
    filename = generated.json()["filename"]

    # Buat atau login User B dengan identitas berbeda.
    email_b = "doc-other@test.ac.id"
    password_b = "password123"

    registered = client.post(
        "/api/v1/auth/register",
        json={"email": email_b, "password": password_b},
    )

    if registered.status_code not in (201,):
        assert registered.status_code in (400, 409), registered.text

    _approve_all()

    login_b = client.post(
        "/api/v1/auth/login",
        data={"username": email_b, "password": password_b},
    )
    assert login_b.status_code == 200, login_b.text
    token_b = login_b.json()["access_token"]

    # User A tetap dapat mengunduh dokumennya.
    download_a = client.get(
        f"/api/v1/documents/download/{filename}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert download_a.status_code == 200

    # User B tidak boleh mengunduh dokumen milik User A.
    download_b = client.get(
        f"/api/v1/documents/download/{filename}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert download_b.status_code == 404


def test_generated_user_directory_rejects_cross_user_symlink(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.services import document_service

    monkeypatch.setattr(document_service, "STORAGE_DIR", tmp_path)

    user_a = str(uuid4())
    user_b = str(uuid4())

    generated = tmp_path / "generated"
    generated.mkdir()

    user_b_dir = generated / user_b
    user_b_dir.mkdir()

    (generated / user_a).symlink_to(user_b_dir, target_is_directory=True)

    try:
        document_service._user_storage_dir(user_a)
    except ValueError:
        pass
    else:
        raise AssertionError("Symlink lintas pengguna seharusnya ditolak.")


def test_generated_file_symlink_is_rejected(tmp_path, monkeypatch):
    from uuid import uuid4
    from app.services import document_service

    monkeypatch.setattr(document_service, "STORAGE_DIR", tmp_path)

    user_id = str(uuid4())
    user_dir = document_service._user_storage_dir(user_id)

    real_file = user_dir / "dokumen_asli.docx"
    real_file.write_bytes(b"PK-test")

    linked_file = user_dir / "dokumen_link.docx"
    linked_file.symlink_to(real_file)

    assert document_service.resolve_stored_file(
        "dokumen_link.docx", user_id
    ) is None
