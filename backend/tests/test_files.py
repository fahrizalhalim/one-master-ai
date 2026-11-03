"""Tes upload & ekstraksi file."""
import io
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_files.db")

from docx import Document  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal
from app.models.user import User  # noqa: E402

client = TestClient(app)

EMAIL = "file@test.ac.id"
PASSWORD = "password123"


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


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_upload_txt():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("catatan.txt", io.BytesIO("Ini isi catatan penting.".encode()), "text/plain")},
        headers=_auth(t),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["chars"] > 0
    assert data["filename"] == "catatan.txt"


def test_upload_docx():
    t = _token()
    # buat DOCX di memori
    doc = Document()
    doc.add_heading("Judul Dokumen", level=1)
    doc.add_paragraph("Isi paragraf satu.")
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("dok.docx", buf, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=_auth(t),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["chars"] > 10

    # ambil teks
    r2 = client.get(f"/api/v1/files/{data['id']}", headers=_auth(t))
    assert r2.status_code == 200
    assert "Judul Dokumen" in r2.json()["total_text"]


def test_upload_reject_bad_ext():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("virus.exe", io.BytesIO(b"MZ..."), "application/x-msdownload")},
        headers=_auth(t),
    )
    assert r.status_code == 400


def test_upload_requires_token():
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
    )
    assert r.status_code in (401, 403)


def test_file_isolation():
    t = _token()
    r = client.post(
        "/api/v1/files/upload",
        files={"file": ("privat.txt", io.BytesIO("rahasia".encode()), "text/plain")},
        headers=_auth(t),
    )
    fid = r.json()["id"]

    # user lain tidak bisa akses
    r2 = client.post(
        "/api/v1/auth/register",
        json={"email": "lain@test.ac.id", "password": "password123"},
    )
    t2 = r2.json()["access_token"]
    r3 = client.get(f"/api/v1/files/{fid}", headers=_auth(t2))
    assert r3.status_code == 404
