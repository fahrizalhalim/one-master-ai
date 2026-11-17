import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TelegramCentralLink(Base):
    __tablename__ = "telegram_central_links"

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            name="uq_telegram_central_links_user_id",
        ),
        UniqueConstraint(
            "telegram_user_id",
            name="uq_telegram_central_links_telegram_user_id",
        ),
        UniqueConstraint(
            "link_code_hash",
            name="uq_telegram_central_links_link_code_hash",
        ),
        Index(
            "ix_telegram_central_links_active",
            "is_active",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
    )

    telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    telegram_username: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations_orm.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Hanya hash kode pengaitan yang disimpan.
    link_code_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    link_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    linked_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
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
