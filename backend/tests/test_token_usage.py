"""Tes pencatatan dan statistik token historis."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.database import Base, engine, SessionLocal
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.auth_service import hash_password

client = TestClient(app)
PW = "password123"


def _create_user(role="user"):
    Base.metadata.create_all(bind=engine)

    email = f"token-{uuid.uuid4().hex}@test.ac.id"
    db = SessionLocal()

    try:
        user = User(
            email=email,
            password_hash=hash_password(PW),
            role=role,
            status="active",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return email, user.id
    finally:
        db.close()


def _login(email):
    response = client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": PW},
    )
    assert response.status_code == 200, response.text

    return {
        "Authorization": f"Bearer {response.json()['access_token']}"
    }


def _insert_usage(user_id, prompt=100, completion=50, total=150):
    db = SessionLocal()

    try:
        db.add(
            TokenUsage(
                user_id=user_id,
                skill="academic",
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                # Statistik historis tidak otomatis menjadi kuota paket.
                package_tokens=0,
            )
        )
        db.commit()
    finally:
        db.close()


def test_stats_contains_total_tokens():
    admin_email, _ = _create_user(role="admin")
    headers = _login(admin_email)

    before = client.get(
        "/api/v1/admin/stats",
        headers=headers,
    )
    assert before.status_code == 200, before.text

    email, user_id = _create_user()
    _insert_usage(user_id)

    after = client.get(
        "/api/v1/admin/stats",
        headers=headers,
    )

    assert after.status_code == 200, after.text
    assert (
        after.json()["total_tokens"] - before.json()["total_tokens"]
        == 150
    )


def test_tokens_per_user():
    admin_email, _ = _create_user(role="admin")
    headers = _login(admin_email)

    email, user_id = _create_user()
    _insert_usage(user_id)

    response = client.get(
        "/api/v1/admin/tokens",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    rows = response.json()

    target = next(
        (row for row in rows if row["email"] == email),
        None,
    )

    assert target is not None
    assert target["total_tokens"] == 150
    assert target["prompt_tokens"] == 100
    assert target["completion_tokens"] == 50
    assert target["requests"] == 1

    # Statistik historis dan kuota paket harian harus terpisah.
    assert target["daily_used"] == 0
    assert target["daily_limit"] == 50000


def test_tokens_summary():
    admin_email, _ = _create_user(role="admin")
    headers = _login(admin_email)

    before = client.get(
        "/api/v1/admin/tokens/summary?days=30",
        headers=headers,
    )
    assert before.status_code == 200, before.text

    _, user_id = _create_user()
    _insert_usage(user_id)

    after = client.get(
        "/api/v1/admin/tokens/summary?days=30",
        headers=headers,
    )

    assert after.status_code == 200, after.text
    data = after.json()

    assert data["total_all_time"] - before.json()["total_all_time"] == 150
    assert isinstance(data["per_day"], list)


def test_tokens_require_admin():
    user_email, _ = _create_user(role="user")
    headers = _login(user_email)

    response = client.get(
        "/api/v1/admin/tokens",
        headers=headers,
    )

    assert response.status_code == 403
