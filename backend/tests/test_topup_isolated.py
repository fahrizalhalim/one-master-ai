import uuid
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user
from app.models.database import Base, get_db
from app.models.user import User
from app.models.subscription import (
    SubscriptionPlan,
    UserSubscription,
    TokenWallet,
    TokenTransaction,
)
from app.models.topup import TopUpRequest

# Daftarkan model agar seluruh foreign key tersedia.
from app.models.conversation import Conversation, Message
from app.models.telegram_account import TelegramAccount
from app.models.telegram_update import TelegramUpdate
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult

from app.api import routes_topup


PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-content"


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    TestingSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    db = TestingSession()

    user = User(
        id=str(uuid.uuid4()),
        email="user@test.local",
        password_hash="test",
        full_name="Test User",
        role="user",
        status="active",
        is_active=True,
    )
    admin = User(
        id=str(uuid.uuid4()),
        email="admin@test.local",
        password_hash="test",
        full_name="Test Admin",
        role="admin",
        status="active",
        is_active=True,
    )
    db.add_all([user, admin])
    db.commit()

    # SQLite tidak mendukung PostgreSQL ON CONFLICT melalui pg_insert.
    # Ganti hanya helper wallet selama pengujian.
    def sqlite_get_wallet(session, user_id):
        wallet = (
            session.query(TokenWallet)
            .filter(TokenWallet.user_id == user_id)
            .first()
        )
        if wallet is None:
            wallet = TokenWallet(
                user_id=user_id,
                topup_balance=0,
            )
            session.add(wallet)
            session.flush()
        return wallet

    monkeypatch.setattr(routes_topup, "_get_wallet", sqlite_get_wallet)
    monkeypatch.setattr(routes_topup, "PROOF_DIR", tmp_path / "proofs")

    app = FastAPI()
    app.include_router(routes_topup.router)

    actor = {"user": user}

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: actor["user"]

    client = TestClient(app)

    yield {
        "db": db,
        "client": client,
        "user": user,
        "admin": admin,
        "actor": actor,
    }

    client.close()
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def make_request(db, user, package_code="starter", token_amount=100000):
    item = TopUpRequest(
        id=str(uuid.uuid4()),
        user_id=user.id,
        package_code=package_code,
        amount_idr=10000,
        token_amount=token_amount,
        payment_method="qris",
        proof_file="proof.png",
        status="pending",
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def wallet_balance(db, user_id):
    wallet = (
        db.query(TokenWallet)
        .filter(TokenWallet.user_id == user_id)
        .first()
    )
    return wallet.topup_balance if wallet else 0


def test_packages_only_return_canonical_codes(env):
    response = env["client"].get("/api/v1/topup/packages")

    assert response.status_code == 200
    codes = [item["code"] for item in response.json()["packages"]]

    assert codes == [
        "starter",
        "regular",
        "professional",
        "business",
    ]


def test_create_request_normalizes_package_alias(env):
    response = env["client"].post(
        "/api/v1/topup/requests",
        data={"package_code": "  BASIC  "},
        files={"proof": ("proof.png", PNG, "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["request"]["package_code"] == "starter"

    item = env["db"].query(TopUpRequest).one()
    assert item.package_code == "starter"
    assert item.status == "pending"


def test_create_request_rejects_unknown_package(env):
    response = env["client"].post(
        "/api/v1/topup/requests",
        data={"package_code": "unknown"},
        files={"proof": ("proof.png", PNG, "image/png")},
    )

    assert response.status_code == 400
    assert env["db"].query(TopUpRequest).count() == 0


def test_create_request_rejects_invalid_file_signature(env):
    response = env["client"].post(
        "/api/v1/topup/requests",
        data={"package_code": "starter"},
        files={"proof": ("proof.png", b"not-a-real-png", "image/png")},
    )

    assert response.status_code == 400
    assert env["db"].query(TopUpRequest).count() == 0


def test_approval_credits_wallet_and_activates_daily_plan(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]
    item = make_request(db, user)

    env["actor"]["user"] = env["admin"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "approved", "admin_note": "Pembayaran valid"},
    )

    assert response.status_code == 200
    assert response.json()["subscription_changed"] is True
    assert response.json()["request"]["status"] == "approved"

    assert wallet_balance(db, user.id) == 100000

    transactions = (
        db.query(TokenTransaction)
        .filter(TokenTransaction.user_id == user.id)
        .all()
    )
    assert len(transactions) == 1
    assert transactions[0].transaction_type == "topup"
    assert transactions[0].amount == 100000

    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.code == "starter")
        .one()
    )
    assert plan.daily_token_limit == 50000

    subscription = (
        db.query(UserSubscription)
        .filter(UserSubscription.user_id == user.id)
        .one()
    )
    assert subscription.status == "active"
    assert subscription.daily_token_limit == 50000


def test_approval_cannot_credit_twice(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]
    item = make_request(db, user)

    env["actor"]["user"] = env["admin"]
    url = f"/api/v1/topup/admin/requests/{item.id}/review"
    body = {"decision": "approved"}

    first = client.post(url, json=body)
    assert first.status_code == 200

    second = client.post(url, json=body)
    assert second.status_code == 409

    assert wallet_balance(db, user.id) == 100000
    assert (
        db.query(TokenTransaction)
        .filter(TokenTransaction.user_id == user.id)
        .count()
        == 1
    )


def test_rejection_does_not_credit_wallet_or_activate_plan(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]
    item = make_request(db, user)

    env["actor"]["user"] = env["admin"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "rejected", "admin_note": "Bukti tidak valid"},
    )

    assert response.status_code == 200
    assert response.json()["request"]["status"] == "rejected"
    assert wallet_balance(db, user.id) == 0
    assert db.query(TokenTransaction).count() == 0
    assert db.query(SubscriptionPlan).count() == 0
    assert db.query(UserSubscription).count() == 0


def test_legacy_package_alias_is_accepted_during_approval(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]
    item = make_request(
        db,
        user,
        package_code="PRO",
        token_amount=250000,
    )

    env["actor"]["user"] = env["admin"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "approved"},
    )

    assert response.status_code == 200
    assert wallet_balance(db, user.id) == 250000

    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.code == "regular")
        .one()
    )
    assert plan.daily_token_limit == 50000


def test_lower_package_does_not_downgrade_active_business(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]

    business = SubscriptionPlan(
        id=str(uuid.uuid4()),
        code="business",
        name="Bisnis",
        description="Paket Bisnis",
        price_idr=100000,
        daily_token_limit=150000,
        duration_days=30,
        is_active=True,
    )
    db.add(business)
    db.flush()

    now = routes_topup.wit_now()
    active = UserSubscription(
        id=str(uuid.uuid4()),
        user_id=user.id,
        plan_id=business.id,
        status="active",
        daily_token_limit=150000,
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=29),
    )
    db.add(active)
    item = make_request(db, user, package_code="starter")
    db.commit()

    env["actor"]["user"] = env["admin"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "approved"},
    )

    assert response.status_code == 200
    assert response.json()["subscription_changed"] is False
    assert wallet_balance(db, user.id) == 100000

    db.refresh(active)
    assert active.status == "active"
    assert active.plan_id == business.id


def test_unknown_legacy_package_rolls_back_approval(env):
    db = env["db"]
    client = env["client"]
    user = env["user"]
    item = make_request(
        db,
        user,
        package_code="unknown-old-package",
    )

    env["actor"]["user"] = env["admin"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "approved"},
    )

    assert response.status_code == 409

    db.refresh(item)
    assert item.status == "pending"
    assert wallet_balance(db, user.id) == 0
    assert db.query(TokenTransaction).count() == 0
    assert db.query(SubscriptionPlan).count() == 0


def test_non_admin_cannot_review_topup(env):
    db = env["db"]
    client = env["client"]
    item = make_request(db, env["user"])

    env["actor"]["user"] = env["user"]

    response = client.post(
        f"/api/v1/topup/admin/requests/{item.id}/review",
        json={"decision": "approved"},
    )

    assert response.status_code == 403
    assert wallet_balance(db, env["user"].id) == 0
