"""Tes kuota token harian WIT."""

import uuid
from datetime import timedelta, timezone, datetime

from fastapi.testclient import TestClient

from app.main import app
from app.models.database import Base, engine, SessionLocal
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.user import User
from app.services.auth_service import hash_password
from app.services.quota_service import current_day_utc_range

client = TestClient(app)
PW = "password123"


def _create_user(role="user"):
    Base.metadata.create_all(bind=engine)

    email = f"rate-{uuid.uuid4().hex}@test.ac.id"
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


def test_quota_endpoint_default_starter():
    email, _ = _create_user()
    headers = _login(email)

    response = client.get("/api/v1/quota", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["unlimited"] is False
    assert data["daily_limit"] == 50000
    assert data["daily_used"] == 0
    assert data["daily_remaining"] == 50000
    assert data["topup_balance"] == 0
    assert data["timezone"] == "Asia/Jayapura"


def test_quota_admin_unlimited():
    email, _ = _create_user(role="admin")
    headers = _login(email)

    response = client.get("/api/v1/quota", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["unlimited"] is True
    assert data["daily_limit"] is None
    assert data["daily_remaining"] is None


def test_quota_counts_package_tokens_and_reservations():
    email, user_id = _create_user()
    headers = _login(email)

    start_utc, _ = current_day_utc_range()
    yesterday = start_utc - timedelta(seconds=1)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    db = SessionLocal()

    try:
        # Pemakaian paket yang sudah selesai.
        db.add(
            TokenUsage(
                user_id=user_id,
                skill="general",
                prompt_tokens=100,
                completion_tokens=0,
                total_tokens=100,
                package_tokens=100,
                created_at=now_utc,
            )
        )

        # Pemakaian hari sebelumnya tidak boleh dihitung.
        db.add(
            TokenUsage(
                user_id=user_id,
                skill="general",
                prompt_tokens=999,
                completion_tokens=0,
                total_tokens=999,
                package_tokens=999,
                created_at=yesterday,
            )
        )

        # Reservasi pending ikut dihitung.
        db.add(
            TokenReservation(
                user_id=user_id,
                reserved_daily=50,
                reserved_topup=200,
                status="pending",
                reference="test-pending",
                description="Test reservasi pending",
                created_at=now_utc,
            )
        )

        # Reservasi needs_review juga ikut dihitung.
        db.add(
            TokenReservation(
                user_id=user_id,
                reserved_daily=25,
                reserved_topup=0,
                status="needs_review",
                reference="test-review",
                description="Test reservasi review",
                created_at=now_utc,
            )
        )

        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/quota", headers=headers)

    assert response.status_code == 200
    data = response.json()

    # 100 pemakaian + 50 pending + 25 needs_review.
    # 999 token kemarin dan 200 token Top Up tidak dihitung.
    assert data["daily_used"] == 175
    assert data["daily_remaining"] == 49825


def test_quota_counts_only_current_wit_day():
    email, user_id = _create_user()
    headers = _login(email)

    start_utc, _ = current_day_utc_range()
    yesterday = start_utc - timedelta(seconds=1)
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    db = SessionLocal()

    try:
        db.add_all([
            TokenUsage(
                user_id=user_id,
                skill="general",
                total_tokens=100,
                package_tokens=100,
                created_at=now_utc,
            ),
            TokenUsage(
                user_id=user_id,
                skill="general",
                total_tokens=500,
                package_tokens=500,
                created_at=yesterday,
            ),
        ])
        db.commit()
    finally:
        db.close()

    response = client.get("/api/v1/quota", headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["daily_used"] == 100
    assert data["daily_remaining"] == 49900
