"""Tes alur approval pendaftaran (Opsi B)."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_approval.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

client = TestClient(app)

ADMIN = "adminal@test.ac.id"
PW = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == ADMIN).first():
            db.add(
                User(email=ADMIN, password_hash=hash_password(PW), role="admin", status="active")
            )
        db.commit()
    finally:
        db.close()


def _admin_headers():
    r = client.post("/api/v1/auth/login", data={"username": ADMIN, "password": PW})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _register(email):
    return client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "full_name": "Calon User"},
    )


def test_register_creates_pending_user():
    r = _register("pending1@test.ac.id")
    assert r.status_code == 201
    assert r.json()["user"]["status"] == "pending"

    # login ditolak dengan pesan jelas
    r2 = client.post(
        "/api/v1/auth/login",
        data={"username": "pending1@test.ac.id", "password": "password123"},
    )
    assert r2.status_code == 403
    assert "MENUNGGU PERSETUJUAN" in r2.json()["detail"]


def test_chat_blocked_for_pending():
    r = _register("pending2@test.ac.id")
    t = r.json()["access_token"]
    r2 = client.post(
        "/api/v1/chat",
        json={"message": "halo"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r2.status_code == 403


def test_admin_approve_flow():
    r = _register("approve@test.ac.id")
    uid = r.json()["user"]["id"]

    # pending list berisi user ini
    h = _admin_headers()
    r2 = client.get("/api/v1/admin/pending", headers=h)
    emails = [u["email"] for u in r2.json()]
    assert "approve@test.ac.id" in emails

    # approve
    r3 = client.post(
        f"/api/v1/admin/users/{uid}/decision",
        json={"decision": "approve"}, headers=h,
    )
    assert r3.status_code == 200
    assert r3.json()["status"] == "active"
    assert r3.json()["is_active"] is True

    # login sekarang sukses
    r4 = client.post(
        "/api/v1/auth/login",
        data={"username": "approve@test.ac.id", "password": "password123"},
    )
    assert r4.status_code == 200


def test_admin_reject_flow():
    r = _register("reject@test.ac.id")
    uid = r.json()["user"]["id"]
    h = _admin_headers()

    r2 = client.post(
        f"/api/v1/admin/users/{uid}/decision",
        json={"decision": "reject"}, headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "rejected"

    # login ditolak dengan pesan rejection
    r3 = client.post(
        "/api/v1/auth/login",
        data={"username": "reject@test.ac.id", "password": "password123"},
    )
    assert r3.status_code == 403
    assert "DITOLAK" in r3.json()["detail"]


def test_non_admin_cannot_decide():
    r = _register("plain@test.ac.id")
    t = r.json()["access_token"]
    uid = r.json()["user"]["id"]
    r2 = client.post(
        f"/api/v1/admin/users/{uid}/decision",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {t}"},
    )
    assert r2.status_code == 403
