"""Endpoint Admin untuk integrasi n8n ONE MASTER AI."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.database import get_db
from app.models.telegram_central_link import TelegramCentralLink
from app.models.user import User
from app.models.automation import (
    AutomationWorkflow,
    AutomationWorkflowAccess,
    AutomationExecution,
)
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.services.n8n_service import (
    N8NServiceError,
    n8n_api_request,
    execute_webhook,
)

router = APIRouter(
    prefix="/api/v1/admin/automation",
    tags=["admin-automation"],
)


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin" or user.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Akses khusus administrator.",
        )
    return user


@router.get("/n8n/workflows")
async def list_n8n_workflows(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2000),
):
    """Membaca metadata workflow n8n khusus Admin."""

    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor

    try:
        result = await n8n_api_request(
            "GET",
            "workflows",
            params=params,
        )
    except N8NServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from None

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="Format respons daftar workflow n8n tidak valid.",
        )

    records = result.get("data", [])
    if not isinstance(records, list):
        raise HTTPException(
            status_code=502,
            detail="Data workflow n8n tidak valid.",
        )

    # Hanya metadata yang diperlukan Admin.
    # Jangan meneruskan nodes, connections, credential, atau data internal.
    workflows = []
    for item in records:
        if not isinstance(item, dict):
            continue

        workflow_id = item.get("id")
        name = item.get("name")

        if not isinstance(workflow_id, str) or not isinstance(name, str):
            continue

        workflows.append({
            "n8n_workflow_id": workflow_id,
            "name": name[:255],
            "active": bool(item.get("active", False)),
            "is_archived": bool(item.get("isArchived", False)),
            "created_at": item.get("createdAt"),
            "updated_at": item.get("updatedAt"),
        })

    return {
        "items": workflows,
        "count": len(workflows),
        "next_cursor": result.get("nextCursor"),
    }


class AutomationImportRequest(BaseModel):
    n8n_workflow_id: str = Field(min_length=1, max_length=100)


@router.post("/registry/import")
async def import_n8n_workflow(
    payload: AutomationImportRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Impor metadata workflow n8n ke registry OM.AI."""

    n8n_id = payload.n8n_workflow_id.strip()

    existing = (
        db.query(AutomationWorkflow)
        .filter(AutomationWorkflow.n8n_workflow_id == n8n_id)
        .first()
    )

    if existing:
        raise HTTPException(
            status_code=409,
            detail="Workflow n8n ini sudah terdaftar di OM.AI.",
        )

    try:
        detail = await n8n_api_request(
            "GET",
            f"workflows/{n8n_id}",
        )
    except N8NServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from None

    if not isinstance(detail, dict):
        raise HTTPException(
            status_code=502,
            detail="Detail workflow n8n tidak valid.",
        )

    returned_id = detail.get("id")
    name = detail.get("name")

    if returned_id != n8n_id or not isinstance(name, str):
        raise HTTPException(
            status_code=502,
            detail="Identitas workflow n8n tidak sesuai.",
        )

    workflow = AutomationWorkflow(
        n8n_workflow_id=n8n_id,
        name=name[:255],
        description="",
        webhook_path=None,
        input_schema={},
        output_schema={},
        access_mode="selected",
        is_published=False,
        is_active=False,
        timeout_seconds=120,
        created_by=admin.id,
    )

    try:
        db.add(workflow)
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Workflow gagal didaftarkan. Periksa database.",
        ) from None

    return {
        "success": True,
        "message": "Workflow berhasil diimpor. Belum dipublikasikan.",
        "item": {
            "id": workflow.id,
            "n8n_workflow_id": workflow.n8n_workflow_id,
            "name": workflow.name,
            "description": workflow.description,
            "is_published": workflow.is_published,
            "is_active": workflow.is_active,
            "webhook_configured": bool(workflow.webhook_path),
        },
    }


# ADMIN_AUTOMATION_MANAGEMENT_V1

class AutomationConfigRequest(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    webhook_path: str | None = Field(default=None, max_length=48)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    access_mode: Literal["all", "selected"] | None = None
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)


class AutomationAccessRequest(BaseModel):
    is_allowed: bool = True


def get_admin_workflow(db: Session, workflow_id: str) -> AutomationWorkflow:
    try:
        workflow_id = str(uuid.UUID(workflow_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Workflow tidak ditemukan.") from None

    workflow = db.get(AutomationWorkflow, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow tidak ditemukan.")
    return workflow


async def verify_n8n_workflow_ready(workflow: AutomationWorkflow) -> None:
    """Pastikan workflow aktif di n8n dan Webhook POST memakai path registry."""

    try:
        detail = await n8n_api_request(
            "GET",
            f"workflows/{workflow.n8n_workflow_id}",
        )
    except N8NServiceError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from None

    if not isinstance(detail, dict):
        raise HTTPException(
            status_code=502,
            detail="Metadata workflow n8n tidak valid.",
        )

    if detail.get("id") != workflow.n8n_workflow_id:
        raise HTTPException(
            status_code=502,
            detail="Identitas workflow n8n tidak sesuai.",
        )

    if detail.get("active") is not True:
        raise HTTPException(
            status_code=409,
            detail="Workflow belum aktif di n8n. Aktifkan workflow di n8n terlebih dahulu.",
        )

    nodes = detail.get("nodes")
    if not isinstance(nodes, list):
        raise HTTPException(
            status_code=502,
            detail="Daftar node workflow n8n tidak tersedia.",
        )

    matching_webhook = any(
        isinstance(node, dict)
        and node.get("type") == "n8n-nodes-base.webhook"
        and isinstance(node.get("parameters"), dict)
        and node["parameters"].get("path") == workflow.webhook_path
        and str(node["parameters"].get("httpMethod", "GET")).upper() == "POST"
        for node in nodes
    )

    if not matching_webhook:
        raise HTTPException(
            status_code=409,
            detail="Node Webhook POST di n8n tidak cocok dengan path registry OM.AI.",
        )


def validate_admin_schema(schema: Any, label: str) -> None:
    if not isinstance(schema, dict):
        raise HTTPException(status_code=422, detail=f"Schema {label} harus berupa objek JSON.")

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        raise HTTPException(
            status_code=422,
            detail=f"Schema {label} tidak valid menurut JSON Schema Draft 2020-12.",
        ) from None


def admin_workflow_data(item: AutomationWorkflow) -> dict[str, Any]:
    return {
        "id": item.id,
        "n8n_workflow_id": item.n8n_workflow_id,
        "name": item.name,
        "description": item.description or "",
        "webhook_configured": bool(item.webhook_path),
        "input_schema": item.input_schema or {},
        "output_schema": item.output_schema or {},
        "access_mode": item.access_mode,
        "is_published": item.is_published,
        "is_active": item.is_active,
        "timeout_seconds": item.timeout_seconds,
        "created_by": item.created_by,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("/registry")
def list_automation_registry(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(AutomationWorkflow)
    total = query.count()
    items = (
        query.order_by(AutomationWorkflow.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [admin_workflow_data(item) for item in items],
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/registry/{workflow_id}/config")
def update_automation_config(
    workflow_id: str,
    payload: AutomationConfigRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)
    changes = payload.dict(exclude_unset=True)

    if workflow.is_published and "webhook_path" in changes:
        raise HTTPException(
            status_code=409,
            detail="Unpublish workflow sebelum mengubah webhook.",
        )

    if "webhook_path" in changes:
        path = changes["webhook_path"]
        if path is not None and not re.fullmatch(r"[0-9a-f]{48}", path):
            raise HTTPException(
                status_code=422,
                detail="Webhook path harus terdiri dari 48 karakter hex lowercase.",
            )

    for key in ("input_schema", "output_schema"):
        if key in changes:
            validate_admin_schema(changes[key], key)

    for key, value in changes.items():
        setattr(workflow, key, value)

    try:
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Konfigurasi workflow gagal disimpan.",
        ) from None

    return {
        "success": True,
        "message": "Konfigurasi workflow berhasil diperbarui.",
        "item": admin_workflow_data(workflow),
    }


@router.post("/registry/{workflow_id}/publish")
async def publish_automation(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)

    if not workflow.webhook_path or not re.fullmatch(
        r"[0-9a-f]{48}", workflow.webhook_path
    ):
        raise HTTPException(
            status_code=422,
            detail="Webhook belum dikonfigurasi dengan path yang valid.",
        )

    validate_admin_schema(workflow.input_schema or {}, "input")
    validate_admin_schema(workflow.output_schema or {}, "output")

    await verify_n8n_workflow_ready(workflow)

    workflow.is_published = True

    try:
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Publikasi gagal disimpan.") from None

    return {
        "success": True,
        "message": "Workflow berhasil dipublikasikan. Aktivasi tetap terpisah.",
        "item": admin_workflow_data(workflow),
    }


@router.post("/registry/{workflow_id}/unpublish")
def unpublish_automation(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)
    workflow.is_active = False
    workflow.is_published = False

    try:
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unpublish gagal disimpan.") from None

    return {
        "success": True,
        "message": "Workflow dinonaktifkan dan tidak lagi dipublikasikan.",
        "item": admin_workflow_data(workflow),
    }


@router.post("/registry/{workflow_id}/activate")
async def activate_automation(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)

    if not workflow.is_published or not workflow.webhook_path:
        raise HTTPException(
            status_code=409,
            detail="Publikasikan workflow dan konfigurasi webhook terlebih dahulu.",
        )

    await verify_n8n_workflow_ready(workflow)

    workflow.is_active = True

    try:
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Aktivasi gagal disimpan.") from None

    return {
        "success": True,
        "message": "Workflow aktif dan dapat digunakan sesuai aturan akses.",
        "item": admin_workflow_data(workflow),
    }


@router.post("/registry/{workflow_id}/deactivate")
def deactivate_automation(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)
    workflow.is_active = False

    try:
        db.commit()
        db.refresh(workflow)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Deaktivasi gagal disimpan.") from None

    return {
        "success": True,
        "message": "Workflow dinonaktifkan. Riwayat eksekusi tetap dipertahankan.",
        "item": admin_workflow_data(workflow),
    }


@router.get("/registry/{workflow_id}/access")
def list_automation_access(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)

    records = (
        db.query(AutomationWorkflowAccess, User)
        .join(User, User.id == AutomationWorkflowAccess.user_id)
        .filter(AutomationWorkflowAccess.workflow_id == workflow.id)
        .order_by(User.email.asc())
        .all()
    )

    return {
        "workflow_id": workflow.id,
        "access_mode": workflow.access_mode,
        "items": [
            {
                "user_id": access.user_id,
                "email": user.email,
                "full_name": user.full_name,
                "is_allowed": access.is_allowed,
                "granted_by": access.granted_by,
                "created_at": access.created_at,
            }
            for access, user in records
        ],
        "count": len(records),
    }


@router.put("/registry/{workflow_id}/access/{user_id}")
def set_automation_access(
    workflow_id: str,
    user_id: str,
    payload: AutomationAccessRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)

    try:
        user_id = str(uuid.UUID(user_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.") from None

    target = db.get(User, user_id)
    if target is None or not target.is_active or target.status != "active":
        raise HTTPException(
            status_code=422,
            detail="Pengguna tidak ditemukan atau akunnya belum aktif.",
        )

    access = (
        db.query(AutomationWorkflowAccess)
        .filter(
            AutomationWorkflowAccess.workflow_id == workflow.id,
            AutomationWorkflowAccess.user_id == target.id,
        )
        .first()
    )

    if access is None:
        access = AutomationWorkflowAccess(
            workflow_id=workflow.id,
            user_id=target.id,
            is_allowed=payload.is_allowed,
            granted_by=admin.id,
        )
        db.add(access)
    else:
        access.is_allowed = payload.is_allowed
        access.granted_by = admin.id

    try:
        db.commit()
        db.refresh(access)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Hak akses gagal disimpan.") from None

    return {
        "success": True,
        "workflow_id": workflow.id,
        "user_id": target.id,
        "is_allowed": access.is_allowed,
    }


@router.delete("/registry/{workflow_id}/access/{user_id}")
def revoke_automation_access(
    workflow_id: str,
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = get_admin_workflow(db, workflow_id)

    access = (
        db.query(AutomationWorkflowAccess)
        .filter(
            AutomationWorkflowAccess.workflow_id == workflow.id,
            AutomationWorkflowAccess.user_id == user_id,
        )
        .first()
    )

    if access is None:
        raise HTTPException(status_code=404, detail="Hak akses tidak ditemukan.")

    db.delete(access)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Pencabutan akses gagal.") from None

    return {"success": True, "message": "Akses pengguna berhasil dicabut."}


@router.get("/executions")
def list_all_automation_executions(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    workflow_id: str | None = Query(default=None, max_length=36),
    user_id: str | None = Query(default=None, max_length=36),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(AutomationExecution)

    if status_filter:
        allowed_statuses = {"running", "completed", "failed", "unknown", "queued"}
        if status_filter not in allowed_statuses:
            raise HTTPException(status_code=422, detail="Filter status tidak dikenal.")
        query = query.filter(AutomationExecution.status == status_filter)

    if workflow_id:
        query = query.filter(AutomationExecution.workflow_id == workflow_id)

    if user_id:
        query = query.filter(AutomationExecution.user_id == user_id)

    total = query.count()
    records = (
        query.order_by(AutomationExecution.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "items": [
            {
                "id": item.id,
                "workflow_id": item.workflow_id,
                "workflow_name": item.workflow_name,
                "user_id": item.user_id,
                "status": item.status,
                "request_data": item.request_data,
                "result_data": item.result_data,
                "error_message": item.error_message,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "duration_ms": item.duration_ms,
                "created_at": item.created_at,
            }
            for item in records
        ],
        "count": len(records),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/executions/{execution_id}")
def get_automation_execution_admin(
    execution_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        execution_id = str(uuid.UUID(execution_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="Eksekusi tidak ditemukan.") from None

    item = db.get(AutomationExecution, execution_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Eksekusi tidak ditemukan.")

    return {
        "id": item.id,
        "workflow_id": item.workflow_id,
        "workflow_name": item.workflow_name,
        "user_id": item.user_id,
        "status": item.status,
        "request_data": item.request_data,
        "result_data": item.result_data,
        "error_message": item.error_message,
        "started_at": item.started_at,
        "finished_at": item.finished_at,
        "duration_ms": item.duration_ms,
        "created_at": item.created_at,
    }


class AutomationTestRequest(BaseModel):
    input_data: dict[str, Any] = Field(default_factory=dict)


@router.post("/registry/{workflow_id}/test")
async def test_admin_automation_workflow(
    workflow_id: str,
    payload: AutomationTestRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Eksekusi uji workflow khusus Admin; tetap tercatat dalam history."""

    workflow = get_admin_workflow(db, workflow_id)

    if not workflow.webhook_path:
        raise HTTPException(
            status_code=409,
            detail="Webhook path belum dikonfigurasi.",
        )

    input_data = payload.input_data

    try:
        input_size = len(
            __import__("json").dumps(
                input_data, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="Input tidak dapat dikonversi ke JSON.",
        ) from None

    if input_size > 65536:
        raise HTTPException(
            status_code=413,
            detail="Input pengujian melebihi batas 64 KB.",
        )

    if workflow.input_schema:
        try:
            Draft202012Validator.check_schema(workflow.input_schema)
            Draft202012Validator(workflow.input_schema).validate(input_data)
        except SchemaError:
            raise HTTPException(
                status_code=503,
                detail="Input Schema workflow tidak valid.",
            ) from None
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="Input tidak sesuai schema: " + str(exc.message)[:500],
            ) from None

    # Workflow harus aktif di n8n dan memiliki Webhook POST
    # yang path-nya sama dengan konfigurasi registry.
    await verify_n8n_workflow_ready(workflow)

    execution = AutomationExecution(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        user_id=admin.id,
        correlation_id=str(uuid.uuid4()),
        status="running",
        request_data={"input_keys": sorted(input_data.keys()), "admin_test": True},
        started_at=datetime.utcnow(),
    )

    db.add(execution)
    try:
        db.commit()
        db.refresh(execution)
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Catatan pengujian gagal dibuat.",
        ) from None

    start = execution.started_at or datetime.utcnow()

    try:
        result = await execute_webhook(
            workflow.webhook_path,
            input_data,
            workflow.timeout_seconds,
        )

        if workflow.output_schema:
            try:
                Draft202012Validator.check_schema(workflow.output_schema)
                Draft202012Validator(workflow.output_schema).validate(result)
            except SchemaError:
                raise RuntimeError("Output Schema workflow tidak valid.")
            except ValidationError as exc:
                raise RuntimeError(
                    "Output tidak sesuai schema: " + str(exc.message)[:500]
                )

        try:
            output_size = len(
                __import__("json").dumps(
                    result, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
        except (TypeError, ValueError):
            raise RuntimeError("Hasil workflow tidak dapat disimpan sebagai JSON.")

        if output_size > 65536:
            raise RuntimeError("Hasil pengujian melebihi batas 64 KB.")

        execution.result_data = (
            result if isinstance(result, dict) else {"output": result}
        )
        execution.status = "completed"

    except Exception as exc:
        execution.status = (
            "unknown" if isinstance(exc, N8NServiceError)
            and "timeout" in str(exc).lower()
            else "failed"
        )
        execution.error_message = str(exc)[:2000]
        execution.result_data = None

    finally:
        execution.finished_at = datetime.utcnow()
        execution.duration_ms = max(
            0,
            int((execution.finished_at - start).total_seconds() * 1000),
        )
        try:
            db.commit()
            db.refresh(execution)
        except Exception:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail="Status hasil pengujian gagal disimpan.",
            ) from None

    return {
        "success": execution.status == "completed",
        "execution": {
            "id": execution.id,
            "workflow_id": execution.workflow_id,
            "workflow_name": execution.workflow_name,
            "status": execution.status,
            "result_data": execution.result_data,
            "error_message": execution.error_message,
            "duration_ms": execution.duration_ms,
            "started_at": execution.started_at,
            "finished_at": execution.finished_at,
        },
    }


@router.delete("/registry/{workflow_id}")
def delete_automation_registry_workflow(
    workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    workflow = (
        db.query(AutomationWorkflow)
        .filter(AutomationWorkflow.id == workflow_id)
        .first()
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Workflow registry tidak ditemukan.")

    wf_name = workflow.name
    try:
        db.delete(workflow)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Gagal menghapus workflow dari Registry OM.AI.",
        ) from None

    return {
        "success": True,
        "message": f"Workflow '{wf_name}' berhasil dihapus dari Registry OM.AI.",
    }


@router.delete("/n8n/workflows/{n8n_workflow_id}")
async def delete_n8n_server_workflow(
    n8n_workflow_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    clean_id = n8n_workflow_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", clean_id):
        raise HTTPException(status_code=400, detail="ID workflow n8n tidak valid.")

    existing = (
        db.query(AutomationWorkflow)
        .filter(AutomationWorkflow.n8n_workflow_id == clean_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Workflow masih terdaftar di Registry OM.AI. Hapus dari Registry OM.AI terlebih dahulu.",
        )

    await n8n_api_request("DELETE", f"workflows/{clean_id}")

    return {
        "success": True,
        "message": "Workflow berhasil dihapus permanen dari server n8n.",
    }


@router.delete("/executions/{execution_id}")
def delete_automation_execution(
    execution_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    execution = (
        db.query(AutomationExecution)
        .filter(AutomationExecution.id == execution_id)
        .first()
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="Riwayat eksekusi tidak ditemukan.")

    try:
        db.delete(execution)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Gagal menghapus riwayat eksekusi.",
        ) from None

    return {
        "success": True,
        "message": "Riwayat eksekusi berhasil dihapus.",
    }


@router.delete("/executions")
def clear_automation_executions(
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(AutomationExecution)
    if status_filter:
        query = query.filter(AutomationExecution.status == status_filter)

    try:
        deleted_count = query.delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Gagal menghapus riwayat eksekusi.",
        ) from None

    return {
        "success": True,
        "deleted_count": deleted_count,
        "message": f"{deleted_count} riwayat eksekusi berhasil dihapus.",
    }


import secrets as _secrets
from app.services.automation_builder import (
    AutomationBuildRequest, BuilderInputField, BuilderStep,
    build_json_schema, compile_n8n_workflow, generate_plan_with_ai,
)

@router.post("/create-workflow")
async def create_automation_directly(payload: AutomationBuildRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    wf_name, wf_desc, delivery, ack_msg = payload.name.strip(), payload.description.strip(), payload.delivery_mode, payload.ack_message.strip()
    fields, steps = list(payload.input_fields), list(payload.steps)
    if payload.mode == "ai":
        if not payload.ai_prompt.strip():
            raise HTTPException(status_code=422, detail="Tuliskan deskripsi otomatisasi untuk AI.")
        plan = await generate_plan_with_ai(payload.ai_prompt, delivery)
        wf_name = wf_name or str(plan.get("name") or "Otomatisasi AI OM.AI").strip()[:255]
        wf_desc = wf_desc or str(plan.get("description") or payload.ai_prompt[:300]).strip()
        if plan.get("delivery_mode") in ("sync", "async_telegram"):
            delivery = plan["delivery_mode"]
        if plan.get("ack_message"):
            ack_msg = str(plan["ack_message"]).strip()[:1000]
        if isinstance(plan.get("input_fields"), list) and not fields:
            for rf in plan["input_fields"]:
                if isinstance(rf, dict) and rf.get("name"):
                    ft = rf.get("field_type") if rf.get("field_type") in ("string", "number", "boolean") else "string"
                    fields.append(BuilderInputField(name=str(rf["name"])[:64], label=str(rf.get("label") or rf["name"])[:200], field_type=ft, required=bool(rf.get("required", False))))
        if isinstance(plan.get("steps"), list):
            steps = [BuilderStep(type=r["type"], name=str(r.get("name") or "")[:100], config=r.get("config") if isinstance(r.get("config"), dict) else {}) for r in plan["steps"] if isinstance(r, dict) and r.get("type") in ("wait", "rss", "http", "ai", "code", "telegram")]
    if not wf_name:
        raise HTTPException(status_code=422, detail="Nama otomatisasi wajib diisi.")
    wh_path = _secrets.token_hex(24)
    n8n_body = compile_n8n_workflow(wf_name=wf_name, webhook_path=wh_path, delivery_mode=delivery, ack_message=ack_msg, steps=steps)
    try:
        created = await n8n_api_request("POST", "workflows", json=n8n_body)
    except N8NServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    n8n_id = str(created.get("id") or "").strip() if isinstance(created, dict) else ""
    if not n8n_id:
        raise HTTPException(status_code=502, detail="n8n tidak mengembalikan ID workflow.")
    activated = False
    for m, u, b in (("POST", f"workflows/{n8n_id}/activate", None), ("PATCH", f"workflows/{n8n_id}", {"active": True})):
        try:
            await n8n_api_request(m, u, json=b); activated = True; break
        except N8NServiceError:
            continue
    is_live = bool(payload.auto_publish and activated)
    wf = AutomationWorkflow(n8n_workflow_id=n8n_id, name=wf_name[:255], description=wf_desc[:5000], webhook_path=wh_path, input_schema=build_json_schema(fields), output_schema={}, access_mode=payload.access_mode, is_published=is_live, is_active=is_live, timeout_seconds=payload.timeout_seconds, created_by=admin.id)
    try:
        db.add(wf); db.flush()
        if payload.access_mode == "selected":
            for uid in set(payload.allowed_user_ids or [admin.id]):
                db.add(AutomationWorkflowAccess(workflow_id=wf.id, user_id=uid, is_allowed=True, granted_by=admin.id))
        db.commit(); db.refresh(wf)
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Workflow terbuat di n8n, tapi gagal masuk Registry OM.AI.") from None
    return {"success": True, "message": f"Otomatisasi '{wf.name}' berhasil dibuat di n8n" + (" dan langsung aktif!" if is_live else " dan tersimpan di Registry."), "item": admin_workflow_data(wf)}
