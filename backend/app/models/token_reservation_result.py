"""Penyimpanan hasil AI untuk pemulihan settlement token."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TokenReservationResult(Base):
    __tablename__ = "token_reservation_results"

    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'settled', 'needs_review')",
            name="ck_token_reservation_results_status",
        ),
    )

    reservation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("token_reservations.id", ondelete="CASCADE"),
        primary_key=True,
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    assistant_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages_orm.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )

    response_content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    skill: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="general",
    )

    prompt_tokens: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    completion_tokens: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    total_tokens: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="received",
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
