import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class User(Base):
    __tablename__ = "users_jwt"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    profession: Mapped[str] = mapped_column(String(100), default="umum")
    organization: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    # Status persetujuan admin: pending / active / rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Versi sesi untuk mencabut seluruh JWT akun secara serentak.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    api_key: Mapped[Optional[str]] = mapped_column(
        String(128), unique=True, index=True, nullable=True
    )
