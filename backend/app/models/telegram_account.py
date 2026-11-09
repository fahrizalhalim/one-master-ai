import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_telegram_accounts_user_id"),
        UniqueConstraint("bot_id", name="uq_telegram_accounts_bot_id"),
        UniqueConstraint("bot_token_hash", name="uq_telegram_accounts_token_hash"),
        UniqueConstraint("webhook_secret", name="uq_telegram_accounts_webhook_secret"),
        UniqueConstraint("link_code_hash", name="uq_telegram_accounts_link_code_hash"),
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

    bot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bot_username: Mapped[str] = mapped_column(String(64), nullable=False)

    # Bot Token hanya disimpan dalam bentuk ciphertext dan hash.
    bot_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    bot_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Secret khusus untuk memverifikasi webhook Telegram.
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)

    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations_orm.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Identitas Telegram pemilik setelah proses linking berhasil.
    telegram_owner_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    telegram_owner_username: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    # Kode linking hanya disimpan dalam bentuk hash.
    link_code_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    link_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

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
