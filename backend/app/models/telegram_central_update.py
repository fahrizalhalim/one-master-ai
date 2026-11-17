import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TelegramCentralUpdate(Base):
    __tablename__ = "telegram_central_updates"

    __table_args__ = (
        UniqueConstraint(
            "update_id",
            name="uq_telegram_central_updates_update_id",
        ),
        CheckConstraint(
            "status IN "
            "('processing', 'reply_pending', 'completed', "
            "'failed', 'needs_review')",
            name="ck_telegram_central_updates_status",
        ),
        Index(
            "ix_telegram_central_updates_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    update_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    telegram_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="SET NULL"),
        nullable=True,
    )

    reservation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("token_reservations.id", ondelete="SET NULL"),
        nullable=True,
    )

    assistant_message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages_orm.id", ondelete="SET NULL"),
        nullable=True,
    )

    response_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="processing",
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
