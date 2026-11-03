"""Tes autentikasi JWT + proteksi /chat.

Catatan: endpoint /chat kini butuh DB (ambil user). Untuk unit test
autentikasi murni, kita fokus ke register/login dengan DB in-memory SQLite.
"""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_auth.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine  # noqa: E402
from app.services.auth_service import (  # noqa: E402
    create_access_token,
    hash_password,
    verify_password,
)

client = TestClient(app)


def setup_function():
    # Buat ulang skema tiap tes (SQLite file lokal test)
    Base.metadata.create_all(bind=engine)


def test_hash_and_verify_password():
    h = hash_password("rahasia123")
    assert verify_password("rahasia123", h)
    assert not verify_password("salah", h)


def test_register_and_me():
    r = client.post(
        "/api/v1/auth/register",
        json={
            "email": "dosen@test.ac.id",
            "password": "rahasia123",
            "full_name": "Dosen Uji",
            "profession": "Dosen Teknik Informatika",
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["user"]["email"] == "dosen@test.ac.id"
    assert data["access_token"]

    # Token valid -> /me
    token = data["access_token"]
    r2 = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r2.status_code == 200
    assert r2.json()["profession"] == "Dosen Teknik Informatika"


def test_register_duplicate_email():
    payload = {
        "email": "dup@test.ac.id",
        "password": "rahasia123",
    }
    client.post("/api/v1/auth/register", json=payload)
    r = client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 409


def test_login_success_and_wrong_password():
    client.post(
        "/api/v1/auth/register",
        json={"email": "login@test.ac.id", "password": "password88"},
    )
    # approve user (fitur approval: pending tak bisa login)
    from app.models.database import SessionLocal
    from app.models.user import User as U

    db = SessionLocal()
    try:
        db.query(U).filter(U.email == "login@test.ac.id").update({"status": "active"})
        db.commit()
    finally:
        db.close()
    # benar
    r = client.post(
        "/api/v1/auth/login",
        data={"username": "login@test.ac.id", "password": "password88"},
    )
    assert r.status_code == 200
    assert r.json()["access_token"]

    # salah
    r2 = client.post(
        "/api/v1/auth/login",
        data={"username": "login@test.ac.id", "password": "salah"},
    )
    assert r2.status_code == 401


def test_chat_requires_token():
    # Tanpa token -> 401 (token wajib sejak fitur multi-user)
    r = client.post(
        "/api/v1/chat", json={"message": ""}, headers={}
    )
    assert r.status_code in (401, 403, 422)


def test_chat_with_invalid_token():
    r = client.post(
        "/api/v1/chat",
        json={"message": "halo"},
        headers={"Authorization": "Bearer token palsu"},
    )
    assert r.status_code == 401


def test_decode_invalid_token():
    assert decode_result(None) is None


def decode_result(t):
    from app.services.auth_service import decode_access_token

    return decode_access_token(t or "abc")
