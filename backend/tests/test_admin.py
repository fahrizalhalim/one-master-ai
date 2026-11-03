"""Tes panel admin: guard role, kelola user, kelola skill."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_admin.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth_service import hash_password  # noqa: E402
from app.models.database import SessionLocal  # noqa: E402

client = TestClient(app)

ADMIN = "admin@test.ac.id"
USER = "biasa@test.ac.id"
PW = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.email == ADMIN).first():
            db.add(User(email=ADMIN, password_hash=hash_password(PW), role="admin", status="active", full_name="Admin"))
        if not db.query(User).filter(User.email == USER).first():
            db.add(User(email=USER, password_hash=hash_password(PW), role="user", status="active", full_name="Biasa"))
        db.commit()
    finally:
        db.close()


def _login(email):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": PW})
    return r.json()["access_token"]


def test_admin_endpoints_require_admin_role():
    t = _login(USER)  # user biasa
    r = client.get("/api/v1/admin/stats", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 403


def test_admin_stats_and_users():
    t = _login(ADMIN)
    h = {"Authorization": f"Bearer {t}"}

    r = client.get("/api/v1/admin/stats", headers=h)
    assert r.status_code == 200
    assert r.json()["total_users"] >= 2

    r2 = client.get("/api/v1/admin/users", headers=h)
    assert r2.status_code == 200
    emails = [u["email"] for u in r2.json()]
    assert ADMIN in emails and USER in emails


def test_admin_toggle_active_and_role():
    ta = _login(ADMIN)
    ha = {"Authorization": f"Bearer {ta}"}
    users = client.get("/api/v1/admin/users", headers=ha).json()
    target = next(u for u in users if u["email"] == USER)

    # nonaktifkan
    r = client.patch(
        f"/api/v1/admin/users/{target['id']}/active",
        json={"is_active": False}, headers=ha,
    )
    assert r.status_code == 200 and r.json()["is_active"] is False

    # user nonaktif tak bisa login
    r2 = client.post("/api/v1/auth/login", data={"username": USER, "password": PW})
    assert r2.status_code == 403

    # aktifkan lagi
    r3 = client.patch(
        f"/api/v1/admin/users/{target['id']}/active",
        json={"is_active": True}, headers=ha,
    )
    assert r3.json()["is_active"] is True

    # angkat jadi admin lalu turunkan lagi
    r4 = client.patch(
        f"/api/v1/admin/users/{target['id']}/role",
        json={"role": "admin"}, headers=ha,
    )
    assert r4.json()["role"] == "admin"
    r5 = client.patch(
        f"/api/v1/admin/users/{target['id']}/role",
        json={"role": "user"}, headers=ha,
    )
    assert r5.json()["role"] == "user"


def test_admin_cannot_deactivate_self():
    ta = _login(ADMIN)
    ha = {"Authorization": f"Bearer {ta}"}
    users = client.get("/api/v1/admin/users", headers=ha).json()
    me = next(u for u in users if u["email"] == ADMIN)
    r = client.patch(
        f"/api/v1/admin/users/{me['id']}/active",
        json={"is_active": False}, headers=ha,
    )
    assert r.status_code == 400


def test_admin_list_skills():
    ta = _login(ADMIN)
    h = {"Authorization": f"Bearer {ta}"}
    r = client.get("/api/v1/admin/skills", headers=h)
    assert r.status_code == 200
    names = [s["name"] for s in r.json()]
    assert "academic" in names  # skill bawaan

    r2 = client.get("/api/v1/admin/skills/academic", headers=h)
    assert r2.status_code == 200
    assert "OBE" in r2.json()["content"]


def test_reset_password():
    ta = _login(ADMIN)
    ha = {"Authorization": f"Bearer {ta}"}
    users = client.get("/api/v1/admin/users", headers=ha).json()
    target = next(u for u in users if u["email"] == USER)

    r = client.post(
        f"/api/v1/admin/users/{target['id']}/reset-password",
        json={"new_password": "passwordRahasiaBaru9"}, headers=ha,
    )
    assert r.status_code == 200

    # login dengan password baru
    r2 = client.post(
        "/api/v1/auth/login",
        data={"username": USER, "password": "passwordRahasiaBaru9"},
    )
    assert r2.status_code == 200
