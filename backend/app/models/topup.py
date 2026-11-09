"""Model permintaan Top Up token ONE MASTER AI."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class TopUpRequest(Base):
    __tablename__ = "topup_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4())
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    package_code: Mapped[str] = mapped_column(String(30), nullable=False)
    amount_idr: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    payment_method: Mapped[str] = mapped_column(
        String(20), nullable=False, default="qris"
    )

    proof_file: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )

    admin_note: Mapped[str] = mapped_column(Text, default="")

    reviewed_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id"),
        nullable=True,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
