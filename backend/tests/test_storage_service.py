from datetime import datetime, timedelta
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.models.user import User
from app.models.subscription import (
    SubscriptionPlan,
    UserSubscription,
    TokenTransaction,
)
from app.models.topup import TopUpRequest
from app.services.storage_service import (
    MB,
    POLICY_START_UTC,
    STORAGE_QUOTAS,
    calculate_storage_usage,
    get_storage_policy,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def make_user(db, created_at=None, role="user"):
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4()}@test.local",
        password_hash="test",
        role=role,
        status="active",
        created_at=created_at or POLICY_START_UTC + timedelta(seconds=1),
    )
    db.add(user)
    db.flush()
    return user


def make_plan(db, code="starter", sort_order=1):
    plan = SubscriptionPlan(
        id=str(uuid.uuid4()),
        code=code,
        name=code.title(),
        token_amount=1000,
        daily_token_limit=1000,
        duration_days=30,
        sort_order=sort_order,
        is_active=True,
    )
    db.add(plan)
    db.flush()
    return plan


def make_subscription(
    db, user, plan, start, end, status="active"
):
    subscription = UserSubscription(
        id=str(uuid.uuid4()),
        user_id=user.id,
        plan_id=plan.id,
        status=status,
        daily_token_limit=plan.daily_token_limit,
        current_period_start=start,
        current_period_end=end,
    )
    db.add(subscription)
    db.flush()
    return subscription


def make_topup(db, user, status="approved", with_transaction=True):
    item = TopUpRequest(
        id=str(uuid.uuid4()),
        user_id=user.id,
        package_code="starter",
        amount_idr=100000,
        token_amount=1000,
        payment_method="qris",
        proof_file="test-proof.png",
        status=status,
    )
    db.add(item)
    db.flush()

    if with_transaction:
        db.add(
            TokenTransaction(
                id=str(uuid.uuid4()),
                user_id=user.id,
                transaction_type="topup",
                amount=1000,
                balance_after=1000,
                reference=f"topup:{item.id}",
                description="Test Top Up",
            )
        )
        db.flush()

    return item


def test_admin_is_exempt(db):
    user = make_user(db, role="admin")

    result = get_storage_policy(
        db, user, now=datetime(2026, 11, 13, 10, 0)
    )

    assert result["status"] == "exempt"
    assert result["upload_allowed"] is True
    assert result["quota_bytes"] is None
    assert result["purge_due"] is False


def test_pre_policy_user_is_exempt(db):
    user = make_user(
        db,
        created_at=POLICY_START_UTC - timedelta(seconds=1),
    )

    result = get_storage_policy(
        db, user, now=datetime(2026, 11, 13, 10, 0)
    )

    assert result["status"] == "exempt"
    assert result["exemption_reason"] == "pre_policy_test_account"
    assert result["upload_allowed"] is True
    assert result["quota_bytes"] is None


def test_user_without_successful_topup_is_not_eligible(db):
    user = make_user(db)

    result = get_storage_policy(
        db, user, now=datetime(2026, 11, 13, 10, 0)
    )

    assert result["status"] == "not_eligible"
    assert result["eligible"] is False
    assert result["upload_allowed"] is True
    assert result["quota_bytes"] is None


def test_approved_topup_without_credit_transaction_is_not_eligible(db):
    user = make_user(db)
    make_topup(db, user, status="approved", with_transaction=False)

    result = get_storage_policy(
        db, user, now=datetime(2026, 11, 13, 10, 0)
    )

    assert result["status"] == "not_eligible"
    assert result["upload_allowed"] is True


def test_rejected_topup_is_not_successful(db):
    user = make_user(db)
    make_topup(db, user, status="rejected", with_transaction=True)

    result = get_storage_policy(
        db, user, now=datetime(2026, 11, 13, 10, 0)
    )

    assert result["status"] == "not_eligible"
    assert result["upload_allowed"] is True


def test_active_subscription_has_correct_package_quota(db):
    user = make_user(db)
    plan = make_plan(db, "regular", 2)
    make_topup(db, user)
    now = datetime(2026, 11, 13, 10, 0)

    make_subscription(
        db, user, plan,
        now - timedelta(days=1),
        now + timedelta(days=29),
    )

    result = get_storage_policy(db, user, now=now)

    assert result["status"] == "active"
    assert result["package_code"] == "regular"
    assert result["quota_bytes"] == 100 * MB
    assert result["quota_mb"] == 100
    assert result["upload_allowed"] is True
    assert result["purge_due"] is False


def test_subscription_time_boundaries(db):
    user = make_user(db)
    plan = make_plan(db)
    make_topup(db, user)

    expires = datetime(2026, 11, 13, 12, 0)
    make_subscription(
        db, user, plan,
        expires - timedelta(days=30),
        expires,
    )

    before_expiry = get_storage_policy(
        db, user, now=expires - timedelta(seconds=1)
    )
    exact_expiry = get_storage_policy(db, user, now=expires)
    exact_grace_end = get_storage_policy(
        db, user, now=expires + timedelta(days=7)
    )
    after_grace = get_storage_policy(
        db, user,
        now=expires + timedelta(days=7, seconds=1),
    )
    exact_purge_day = get_storage_policy(
        db, user, now=expires + timedelta(days=30)
    )

    assert before_expiry["status"] == "active"
    assert exact_expiry["status"] == "grace"
    assert exact_expiry["upload_allowed"] is True
    assert exact_grace_end["status"] == "grace"
    assert exact_grace_end["upload_allowed"] is True
    assert after_grace["status"] == "upload_locked"
    assert after_grace["upload_allowed"] is False
    assert after_grace["purge_due"] is False
    assert exact_purge_day["status"] == "purge_due"
    assert exact_purge_day["purge_due"] is True
    assert exact_purge_day["upload_allowed"] is False


def test_latest_subscription_wins_after_upgrade(db):
    user = make_user(db)
    starter = make_plan(db, "starter", 1)
    regular = make_plan(db, "regular", 2)
    make_topup(db, user)

    now = datetime(2026, 11, 13, 10, 0)

    make_subscription(
        db, user, starter,
        now - timedelta(days=10),
        now + timedelta(days=20),
        status="replaced",
    )
    make_subscription(
        db, user, regular,
        now - timedelta(days=1),
        now + timedelta(days=29),
        status="active",
    )

    result = get_storage_policy(db, user, now=now)

    assert result["status"] == "active"
    assert result["package_code"] == "regular"
    assert result["quota_bytes"] == STORAGE_QUOTAS["regular"]


def test_storage_usage_ignores_sidecars_and_hidden_files(tmp_path):
    (tmp_path / "document.pdf").write_bytes(b"A" * 100)
    (tmp_path / "document.pdf.extract.txt").write_bytes(b"B" * 30)
    (tmp_path / "document.pdf.name.txt").write_bytes(b"C" * 20)
    (tmp_path / ".stage-upload").write_bytes(b"D" * 50)

    assert calculate_storage_usage(tmp_path) == 100


def test_storage_usage_can_exclude_replaced_file(tmp_path):
    (tmp_path / "file123.pdf").write_bytes(b"A" * 100)
    (tmp_path / "other.pdf").write_bytes(b"B" * 40)

    assert calculate_storage_usage(tmp_path) == 140
    assert calculate_storage_usage(
        tmp_path, exclude_file_id="file123"
    ) == 40
