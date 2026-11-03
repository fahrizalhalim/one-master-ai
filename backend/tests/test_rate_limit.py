"""Tes rate limiting harian."""
import os
from datetime import datetime, timezone

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_rate.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal  # noqa: E402
from app.models.token_usage import TokenUsage  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

client = TestClient(app)

USER = "rate@test.ac.id"
ADMIN = "rateadmin@test.ac.id"
PW = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == ADMIN).first():
            db.add(User(email=ADMIN, password_hash=hash_password(PW), role="admin"))
        if not db.query(User).filter(User.email == USER).first():
            db.add(User(email=USER, password_hash=hash_password(PW), role="user"))
        db.commit()
    finally:
        db.close()


def _login(email):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": PW})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _fill_usage(email, n):
    """Isi n catatan token hari ini untuk simulasi pemakaian."""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        for _ in range(n):
            db.add(
                TokenUsage(
                    user_id=user.id,
                    skill="general",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                )
            )
        db.commit()
    finally:
        db.close()


def test_quota_endpoint():
    h = _login(USER)
    r = client.get("/api/v1/quota", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["limit"] == 50  # default
    assert data["used"] == 0
    assert data["remaining"] == 50
    assert data["unlimited"] is False


def test_quota_admin_unlimited():
    h = _login(ADMIN)
    r = client.get("/api/v1/quota", headers=h)
    assert r.status_code == 200
    assert r.json()["unlimited"] is True


def test_chat_blocked_after_limit():
    h = _login(USER)
    # simulasi sudah pakai 50 chat hari ini
    _fill_usage(USER, 50)
    r = client.post("/api/v1/chat", json={"message": "halo"}, headers=h)
    assert r.status_code == 429, r.text
    assert "Kuota harian habis" in r.json()["detail"]

    # quota endpoint juga menunjukkan habis
    r2 = client.get("/api/v1/quota", headers=h)
    assert r2.json()["remaining"] == 0


def test_admin_not_blocked():
    h = _login(ADMIN)
    _fill_usage(ADMIN, 100)  # lebih dari limit user biasa
    # admin chat -> bukan 429 (akan 502 karena dummy AI key, itu bukan rate limit)
    r = client.post("/api/v1/chat", json={"message": "tes admin"}, headers=h)
    assert r.status_code != 429


def test_quota_counts_only_today():
    h = _login(USER)
    # hapus semua usage lalu isi 2 -> used harus 2
    db = SessionLocal()
    try:
        db.query(TokenUsage).delete()
        db.commit()
    finally:
        db.close()
    _fill_usage(USER, 2)
    r = client.get("/api/v1/quota", headers=h)
    assert r.json()["used"] == 2
    assert r.json()["remaining"] == 48
