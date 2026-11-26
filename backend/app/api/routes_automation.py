"""Endpoint automation pengguna ONE MASTER AI."""

import json
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.automation import (
    AutomationExecution,
    AutomationWorkflow,
    AutomationWorkflowAccess,
)
from app.models.database import get_db
from app.models.telegram_central_link import TelegramCentralLink
from app.models.user import User
from app.services.n8n_service import N8NServiceError, execute_webhook

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])

MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_EXECUTIONS_PER_HOUR = 20
MAX_CONCURRENT_EXECUTIONS = 3


class AutomationExecuteRequest(BaseModel):
    input_data: dict[str, Any] = Field(default_factory=dict)


def require_active_user(user: User) -> None:
    if not user.is_active or user.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Akun Anda belum disetujui atau sedang tidak aktif.",
        )


def has_workflow_access(
    db: Session, workflow: AutomationWorkflow, user: User
) -> bool:
    if workflow.access_mode == "all":
        return True
    if workflow.access_mode != "selected":
        return False

    access = (
        db.query(AutomationWorkflowAccess)
        .filter(
            AutomationWorkflowAccess.workflow_id == workflow.id,
            AutomationWorkflowAccess.user_id == user.id,
            AutomationWorkflowAccess.is_allowed.is_(True),
        )
        .first()
    )
    return access is not None


def validate_schema(schema: Any, data: Any, label: str) -> None:
    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=503,
            detail=f"Schema {label} workflow tidak valid.",
        )

    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(data)
    except SchemaError:
        raise HTTPException(
            status_code=503,
            detail=f"Schema {label} workflow belum benar. Hubungi Admin.",
        ) from None
    except ValidationError:
        raise HTTPException(
            status_code=422,
            detail=f"Data tidak sesuai schema {label} workflow.",
        ) from None


def public_execution(item: AutomationExecution) -> dict[str, Any]:
    return {
        "id": item.id,
        "workflow_id": item.workflow_id,
        "workflow_name": item.workflow_name,
        "status": item.status,
        "request_data": item.request_data,
        "result_data": item.result_data,
        "error_message": item.error_message,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "duration_ms": item.duration_ms,
        "created_at": item.created_at,
    }


@router.get("/workflows")
def list_available_workflows(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_active_user(current_user)

    candidates = (
        db.query(AutomationWorkflow)
        .filter(
            AutomationWorkflow.is_published.is_(True),
            AutomationWorkflow.is_active.is_(True),
            AutomationWorkflow.webhook_path.isnot(None),
        )
        .order_by(AutomationWorkflow.name.asc())
        .all()
    )

    items = []
    for workflow in candidates:
        if has_workflow_access(db, workflow, current_user):
            items.append({
                "id": workflow.id,
                "name": workflow.name,
                "description": workflow.description or "",
                "input_schema": workflow.input_schema or {},
                "timeout_seconds": workflow.timeout_seconds,
            })

    return {"items": items, "count": len(items)}


@router.post("/workflows/{workflow_id}/execute")
async def execute_automation_workflow(
    workflow_id: str,
    payload: AutomationExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_active_user(current_user)

    try:
        public_id = str(uuid.UUID(workflow_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Workflow tidak ditemukan.") from None

    workflow = (
        db.query(AutomationWorkflow)
        .filter(AutomationWorkflow.id == public_id)
        .first()
    )

    if (
        workflow is None
        or not workflow.is_published
        or not workflow.is_active
        or not workflow.webhook_path
    ):
        raise HTTPException(status_code=404, detail="Workflow tidak tersedia.")

    if not has_workflow_access(db, workflow, current_user):
        raise HTTPException(status_code=403, detail="Anda tidak memiliki akses ke workflow ini.")

    input_data = payload.input_data
    try:
        input_size = len(
            json.dumps(input_data, ensure_ascii=False).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Format input tidak valid.") from None

    if input_size > MAX_INPUT_BYTES:
        raise HTTPException(status_code=413, detail="Ukuran input maksimum 64 KB.")

    validate_schema(workflow.input_schema or {}, input_data, "input")

    now = datetime.utcnow()

    recent_count = (
        db.query(AutomationExecution)
        .filter(
            AutomationExecution.user_id == current_user.id,
            AutomationExecution.created_at >= now - timedelta(hours=1),
        )
        .count()
    )
    if recent_count >= MAX_EXECUTIONS_PER_HOUR:
        raise HTTPException(status_code=429, detail="Batas 20 eksekusi per jam tercapai.")

    running_count = (
        db.query(AutomationExecution)
        .filter(
            AutomationExecution.user_id == current_user.id,
            AutomationExecution.status.in_(["running", "unknown"]),
        )
        .count()
    )
    if running_count >= MAX_CONCURRENT_EXECUTIONS:
        raise HTTPException(status_code=429, detail="Maksimum 3 eksekusi bersamaan tercapai.")

    execution = AutomationExecution(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        user_id=current_user.id,
        correlation_id=str(uuid.uuid4()),
        status="running",
        # Nilai input tidak disimpan agar data pribadi tidak masuk log riwayat.
        request_data={"input_keys": sorted(input_data.keys())},
        started_at=now,
    )

    try:
        db.add(execution)
        db.commit()
        db.refresh(execution)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Riwayat eksekusi gagal disiapkan.",
        ) from None

    started = time.monotonic()

    webhook_payload = dict(input_data)
    tg_link = (
        db.query(TelegramCentralLink)
        .filter(
            TelegramCentralLink.user_id == current_user.id,
            TelegramCentralLink.is_active.is_(True),
        )
        .first()
    )
    tg_chat_id = tg_link.telegram_user_id if tg_link else None
    webhook_payload.setdefault("telegram_chat_id", tg_chat_id)
    webhook_payload["_omai_context"] = {
        "execution_id": execution.id,
        "user_id": current_user.id,
        "user_email": current_user.email,
        "telegram_chat_id": tg_chat_id,
    }

    try:
        result = await execute_webhook(
            workflow.webhook_path,
            webhook_payload,
            workflow.timeout_seconds,
        )

        if workflow.output_schema:
            validate_schema(workflow.output_schema, result, "output")

        result_size = len(
            json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
        )

        if result_size > MAX_OUTPUT_BYTES:
            stored_result = {
                "message": "Workflow selesai, tetapi hasil melebihi batas tampilan 64 KB."
            }
        elif isinstance(result, dict):
            stored_result = result
        elif isinstance(result, list):
            stored_result = {"items": result}
        else:
            stored_result = {"result": result}

        execution.status = "completed"
        execution.result_data = stored_result
        execution.error_message = None

    except HTTPException as exc:
        execution.status = "failed"
        execution.error_message = str(exc.detail)[:500]

    except N8NServiceError as exc:
        message = str(exc)
        # Timeout tidak berarti proses di sisi n8n telah berhenti.
        is_timeout = (
            "timeout" in message.lower()
            or "batas waktu" in message.lower()
        )
        execution.status = "unknown" if is_timeout else "failed"
        execution.error_message = message[:500]

    except Exception:
        execution.status = "failed"
        execution.error_message = "Eksekusi gagal karena kesalahan internal."

    execution.finished_at = datetime.utcnow()
    execution.duration_ms = int((time.monotonic() - started) * 1000)

    try:
        db.commit()
        db.refresh(execution)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Status eksekusi belum dapat disimpan. Hubungi Admin.",
        ) from None

    return {
        "success": execution.status == "completed",
        "execution": public_execution(execution),
        "message": (
            "Workflow berhasil dijalankan."
            if execution.status == "completed"
            else execution.error_message
            or "Status eksekusi perlu diperiksa administrator."
        ),
    }


@router.get("/executions")
def list_my_executions(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_active_user(current_user)

    records = (
        db.query(AutomationExecution)
        .filter(AutomationExecution.user_id == current_user.id)
        .order_by(AutomationExecution.created_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "items": [public_execution(item) for item in records],
        "count": len(records),
    }


@router.get("/executions/{execution_id}")
def get_my_execution(
    execution_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_active_user(current_user)

    item = (
        db.query(AutomationExecution)
        .filter(
            AutomationExecution.id == execution_id,
            AutomationExecution.user_id == current_user.id,
        )
        .first()
    )

    if item is None:
        raise HTTPException(status_code=404, detail="Riwayat eksekusi tidak ditemukan.")

    return public_execution(item)
