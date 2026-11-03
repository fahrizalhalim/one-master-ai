"""Tes pencatatan & statistik token."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_token.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal  # noqa: E402
from app.models.token_usage import TokenUsage  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402

client = TestClient(app)

ADMIN = "tokadmin@test.ac.id"
PW = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == ADMIN).first():
            db.add(User(email=ADMIN, password_hash=hash_password(PW), role="admin", status="active"))
        db.commit()
    finally:
        db.close()


def _admin_headers():
    r = client.post(
        "/api/v1/auth/login", data={"username": ADMIN, "password": PW}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_stats_contains_total_tokens():
    # sisipkan data token langsung
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == ADMIN).first()
        db.add(
            TokenUsage(
                user_id=user.id,
                skill="academic",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.get("/api/v1/admin/stats", headers=_admin_headers())
    assert r.status_code == 200
    assert r.json()["total_tokens"] >= 150


def test_tokens_per_user():
    h = _admin_headers()
    r = client.get("/api/v1/admin/tokens", headers=h)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    top = rows[0]
    assert top["total_tokens"] >= 150
    assert top["requests"] >= 1


def test_tokens_summary():
    h = _admin_headers()
    r = client.get("/api/v1/admin/tokens/summary?days=30", headers=h)
    assert r.status_code == 200
    data = r.json()
    assert data["total_all_time"] >= 150
    assert isinstance(data["per_day"], list)


def test_tokens_require_admin():
    # register user biasa
    r = client.post(
        "/api/v1/auth/register",
        json={"email": "biasatok@test.ac.id", "password": "password123"},
    )
    t = r.json()["access_token"]
    r2 = client.get(
        "/api/v1/admin/tokens", headers={"Authorization": f"Bearer {t}"}
    )
    assert r2.status_code == 403
