"""Perhitungan kuota token harian berdasarkan zona waktu WIT."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.subscription import SubscriptionPlan, UserSubscription
from app.models.token_reservation import TokenReservation
from app.models.token_usage import TokenUsage
from app.models.user import User

WIT = ZoneInfo("Asia/Jayapura")

# Batas harian paket, termasuk kode paket lama.
DAILY_PACKAGE_LIMITS = {
    "starter": 50000,
    "basic": 50000,
    "regular": 50000,
    "pro": 50000,
    "professional": 150000,
    "premium": 150000,
    "business": 150000,
}


def current_day_utc_range() -> tuple[datetime, datetime]:
    """Batas awal dan akhir hari WIT dalam format UTC-naive."""

    now_wit = datetime.now(WIT)
    start_wit = now_wit.replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_wit = start_wit + timedelta(days=1)

    start_utc = start_wit.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_wit.astimezone(timezone.utc).replace(tzinfo=None)

    return start_utc, end_utc


def effective_daily_token_limit(db: Session, user: User) -> int | None:
    """Batas harian efektif berdasarkan paket aktif pengguna."""

    if user.role == "admin":
        return None

    # Kolom periode langganan disimpan sebagai WIT-naive oleh wit_now().
    # Karena itu, pembandingnya juga harus WIT-naive.
    now_wit = datetime.now(WIT).replace(tzinfo=None)

    subscription = (
        db.query(UserSubscription, SubscriptionPlan)
        .join(
            SubscriptionPlan,
            UserSubscription.plan_id == SubscriptionPlan.id,
        )
        .filter(
            UserSubscription.user_id == user.id,
            UserSubscription.status == "active",
            UserSubscription.current_period_start <= now_wit,
            UserSubscription.current_period_end > now_wit,
            SubscriptionPlan.is_active.is_(True),
        )
        .order_by(UserSubscription.current_period_end.desc())
        .first()
    )

    if subscription:
        _, plan = subscription
        limit = int(plan.daily_token_limit or 0)
        if limit > 0:
            return limit

        return DAILY_PACKAGE_LIMITS.get(plan.code, 50000)

    # Pengguna tanpa langganan aktif memakai paket Starter.
    return DAILY_PACKAGE_LIMITS["starter"]


def daily_token_usage(db: Session, user: User) -> tuple[int | None, int]:
    """
    Mengembalikan batas dan pemakaian token harian.

    Pemakaian mencakup:
    - Token paket dari reservasi yang selesai.
    - Token paket yang masih direservasi (pending/needs_review).

    Token Top Up tidak mengurangi kuota paket harian.
    """

    limit = effective_daily_token_limit(db, user)
    if limit is None:
        return None, 0

    start_utc, end_utc = current_day_utc_range()

    actual_used = (
        db.query(func.coalesce(func.sum(TokenUsage.package_tokens), 0))
        .filter(
            TokenUsage.user_id == user.id,
            TokenUsage.created_at >= start_utc,
            TokenUsage.created_at < end_utc,
        )
        .scalar()
    )

    pending_reserved = (
        db.query(func.coalesce(func.sum(TokenReservation.reserved_daily), 0))
        .filter(
            TokenReservation.user_id == user.id,
            TokenReservation.status.in_(["pending", "needs_review"]),
            TokenReservation.created_at >= start_utc,
            TokenReservation.created_at < end_utc,
        )
        .scalar()
    )

    used = int(actual_used or 0) + int(pending_reserved or 0)
    return limit, used
