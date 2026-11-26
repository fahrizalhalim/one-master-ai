"""Model database integrasi workflow n8n dengan ONE MASTER AI."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


class AutomationWorkflow(Base):
    """Registry workflow yang dikelola Admin melalui OM.AI."""

    __tablename__ = "automation_workflows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    n8n_workflow_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    webhook_path: Mapped[str | None] = mapped_column(
        String(512), unique=True, nullable=True
    )
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    access_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="selected"
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120
    )
    created_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(),
        onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_automation_workflows_published_active",
              "is_published", "is_active"),
    )


class AutomationWorkflowAccess(Base):
    """Hak akses workflow untuk pengguna tertentu."""

    __tablename__ = "automation_workflow_access"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    workflow_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("automation_workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
    )
    is_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    granted_by: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "workflow_id", "user_id",
            name="uq_automation_access_workflow_user"
        ),
        Index("ix_automation_access_user_allowed",
              "user_id", "is_allowed"),
    )


class AutomationExecution(Base):
    """Riwayat eksekusi workflow milik pengguna."""

    __tablename__ = "automation_executions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    workflow_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("automation_workflows.id", ondelete="SET NULL"),
        nullable=True,
    )
    workflow_name: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users_jwt.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False,
        default=lambda: str(uuid.uuid4())
    )
    n8n_execution_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="queued"
    )
    request_data: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    result_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_automation_executions_user_created",
              "user_id", "created_at"),
        Index("ix_automation_executions_workflow_created",
              "workflow_id", "created_at"),
        Index("ix_automation_executions_status",
              "status", "created_at"),
    )
