"""Model reservasi token untuk mencegah saldo terpakai ganda."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TokenReservation(Base):
    __tablename__ = "token_reservations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    subscription_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    reserved_topup: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    reserved_daily: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    actual_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    reference: Mapped[str] = mapped_column(
        String(100), nullable=False, default=""
    )

    description: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
