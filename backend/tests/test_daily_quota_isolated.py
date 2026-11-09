import os
import tempfile
import uuid
from datetime import datetime, timedelta

fd, db_path = tempfile.mkstemp(
    prefix="one_master_daily_", suffix=".db"
)
os.close(fd)

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.models.subscription import (
    SubscriptionPlan,
    UserSubscription,
    TokenWallet,
)
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.services.quota_service import (
    WIT,
    current_day_utc_range,
    daily_token_usage,
)
from app.services.token_service import reserve_tokens

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)


def make_user(db, role="user"):
    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4()}@example.invalid",
        password_hash="test",
        role=role,
        status="active",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def make_wallet(db, user, balance=1000):
    db.add(TokenWallet(
        user_id=user.id,
        topup_balance=balance,
    ))
    db.flush()


def add_usage(db, user, package_tokens, total_tokens=None):
    start, _ = current_day_utc_range()
    db.add(TokenUsage(
        user_id=user.id,
        skill="general",
        prompt_tokens=package_tokens,
        completion_tokens=0,
        total_tokens=total_tokens if total_tokens is not None else package_tokens,
        package_tokens=package_tokens,
        created_at=start + timedelta(hours=1),
    ))
    db.flush()


def add_pending(db, user, daily, topup=0, status="pending"):
    start, _ = current_day_utc_range()
    db.add(TokenReservation(
        id=str(uuid.uuid4()),
        user_id=user.id,
        subscription_id=None,
        reserved_daily=daily,
        reserved_topup=topup,
        actual_tokens=0,
        status=status,
        reference=str(uuid.uuid4()),
        description="Tes kuota harian",
        created_at=start + timedelta(hours=2),
    ))
    db.flush()


def test_daily_limits_and_admin_unlimited():
    db = Session()
    try:
        starter = make_user(db)
        admin = make_user(db, "admin")

        limit, used = daily_token_usage(db, starter)
        assert limit == 50000
        assert used == 0

        limit, used = daily_token_usage(db, admin)
        assert limit is None
        assert used == 0
    finally:
        db.close()


def test_active_business_plan_limit():
    db = Session()
    try:
        user = make_user(db)

        now_wit = datetime.now(WIT).replace(tzinfo=None)
        plan = SubscriptionPlan(
            id=str(uuid.uuid4()),
            code="business",
            name="Bisnis",
            description="Tes",
            price_idr=0,
            daily_token_limit=150000,
            duration_days=30,
            is_active=True,
        )
        db.add(plan)
        db.flush()

        db.add(UserSubscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            daily_token_limit=150000,
            current_period_start=now_wit - timedelta(days=1),
            current_period_end=now_wit + timedelta(days=29),
        ))
        db.commit()

        limit, used = daily_token_usage(db, user)
        assert limit == 150000
        assert used == 0
    finally:
        db.close()


def test_pending_and_needs_review_count_but_topup_does_not():
    db = Session()
    try:
        user = make_user(db)

        add_usage(db, user, 100)
        add_pending(db, user, daily=200, topup=500, status="pending")
        add_pending(db, user, daily=300, topup=700, status="needs_review")
        db.commit()

        limit, used = daily_token_usage(db, user)

        assert limit == 50000
        assert used == 600
    finally:
        db.close()


def test_reservation_uses_daily_quota_before_topup():
    db = Session()
    try:
        user = make_user(db)
        make_wallet(db, user, balance=100)

        add_usage(db, user, 49990)
        db.commit()

        reservation = reserve_tokens(
            db,
            user,
            20,
            "daily-topup-test",
            "Tes pembagian paket dan Top Up",
        )

        assert reservation is not None
        assert reservation.reserved_daily == 10
        assert reservation.reserved_topup == 10

        wallet = db.query(TokenWallet).filter_by(
            user_id=user.id
        ).one()
        assert wallet.topup_balance == 90

        limit, used = daily_token_usage(db, user)
        assert limit == 50000
        assert used == 50000
    finally:
        db.close()


def test_wit_day_range_is_24_hours():
    start_utc, end_utc = current_day_utc_range()

    assert end_utc - start_utc == timedelta(days=1)

    start_wit = start_utc.replace(tzinfo=__import__("datetime").timezone.utc)
    start_wit = start_wit.astimezone(WIT)

    assert start_wit.hour == 0
    assert start_wit.minute == 0
    assert start_wit.second == 0


def setup_module():
    Base.metadata.create_all(bind=engine)


def teardown_module():
    engine.dispose()
    try:
        os.unlink(db_path)
    except FileNotFoundError:
        pass
