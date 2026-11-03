"""Endpoint SuperAdmin: kelola user & skill (khusus role=admin)."""

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.master_agent import SKILLS_DIR
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Akses khusus administrator."
        )
    return user


# ---------- Schemas ----------


class AdminUserItem(BaseModel):
    id: str
    email: str
    full_name: str
    profession: str
    organization: str
    role: str
    status: str
    is_active: bool
    created_at: str

    class Config:
        from_attributes = True


class StatsResponse(BaseModel):
    total_users: int
    active_users: int
    total_conversations: int
    total_messages: int
    total_tokens: int = 0


class SetRoleRequest(BaseModel):
    role: str  # "user" | "admin"


class SetActiveRequest(BaseModel):
    is_active: bool


class ResetPasswordRequest(BaseModel):
    new_password: str


class SkillItem(BaseModel):
    name: str
    instruction_file: str
    chars: int


# ---------- Statistik ----------


@router.get("/stats", response_model=StatsResponse)
def stats(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    total_tokens = (
        db.query(func.coalesce(func.sum(TokenUsage.total_tokens), 0)).scalar()
    )
    return StatsResponse(
        total_users=db.query(User).count(),
        active_users=db.query(User).filter(User.is_active == True).count(),  # noqa: E712
        total_conversations=db.query(Conversation).count(),
        total_messages=db.query(Message).count(),
        total_tokens=int(total_tokens or 0),
    )


# ---------- Token usage per user ----------


@router.get("/tokens")
def token_usage(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Total token per user, urut dari pemakai terbesar."""
    rows = (
        db.query(
            User.email,
            User.full_name,
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total"),
            func.count(TokenUsage.id).label("requests"),
        )
        .outerjoin(TokenUsage, TokenUsage.user_id == User.id)
        .group_by(User.id, User.email, User.full_name)
        .order_by(func.sum(TokenUsage.total_tokens).desc())
        .limit(200)
        .all()
    )
    return [
        {
            "email": r.email,
            "full_name": r.full_name,
            "prompt_tokens": int(r.prompt),
            "completion_tokens": int(r.completion),
            "total_tokens": int(r.total),
            "requests": int(r.requests),
        }
        for r in rows
    ]


@router.get("/tokens/summary")
def token_summary(
    days: int = 30,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Ringkasan token global per hari (default 30 hari terakhir)."""
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(
            func.date(TokenUsage.created_at).label("day"),
            func.sum(TokenUsage.total_tokens).label("total"),
        )
        .filter(TokenUsage.created_at >= since)
        .group_by(func.date(TokenUsage.created_at))
        .order_by(func.date(TokenUsage.created_at))
        .all()
    )
    total_all = (
        db.query(func.coalesce(func.sum(TokenUsage.total_tokens), 0)).scalar()
    )
    return {
        "days": days,
        "per_day": [{"day": str(r.day), "total": int(r.total)} for r in rows],
        "total_all_time": int(total_all or 0),
    }


# ---------- Kelola User ----------


@router.get("/users", response_model=list[AdminUserItem])
def list_users(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    users = db.query(User).order_by(User.created_at.desc()).limit(500).all()
    return [
        AdminUserItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            profession=u.profession,
            organization=u.organization,
            role=u.role,
            status=u.status,
            is_active=u.is_active,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


# ---------- Persetujuan pendaftaran (approval) ----------


class DecisionRequest(BaseModel):
    decision: str  # "approve" | "reject"


@router.get("/pending")
def list_pending(
    admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Daftar pendaftar yang menunggu persetujuan."""
    users = (
        db.query(User)
        .filter(User.status == "pending")
        .order_by(User.created_at.asc())
        .all()
    )
    return [
        AdminUserItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            profession=u.profession,
            organization=u.organization,
            role=u.role,
            status=u.status,
            is_active=u.is_active,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


@router.post("/users/{user_id}/decision", response_model=AdminUserItem)
def decide_user(
    user_id: str,
    data: DecisionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if data.decision not in ("approve", "reject"):
        raise HTTPException(
            status_code=422, detail="decision harus 'approve' atau 'reject'."
        )
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Tidak perlu approve diri sendiri.")

    target.status = "active" if data.decision == "approve" else "rejected"
    # Approval sekaligus pastikan akun aktif; rejection nonaktifkan
    target.is_active = data.decision == "approve"
    db.commit()
    db.refresh(target)
    return AdminUserItem(
        id=target.id,
        email=target.email,
        full_name=target.full_name,
        profession=target.profession,
        organization=target.organization,
        role=target.role,
        status=target.status,
        is_active=target.is_active,
        created_at=target.created_at.isoformat(),
    )

@router.patch("/users/{user_id}/role", response_model=AdminUserItem)
def set_role(
    user_id: str,
    data: SetRoleRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if data.role not in ("user", "admin"):
        raise HTTPException(status_code=422, detail="Role harus 'user' atau 'admin'.")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    if target.id == admin.id and data.role != "admin":
        raise HTTPException(
            status_code=400, detail="Tidak bisa menurunkan role diri sendiri."
        )
    target.role = data.role
    db.commit()
    db.refresh(target)
    return AdminUserItem(
        id=target.id,
        email=target.email,
        full_name=target.full_name,
        profession=target.profession,
        organization=target.organization,
        role=target.role,
        status=target.status,
        is_active=target.is_active,
        created_at=target.created_at.isoformat(),
    )


@router.patch("/users/{user_id}/active", response_model=AdminUserItem)
def set_active(
    user_id: str,
    data: SetActiveRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    if target.id == admin.id and not data.is_active:
        raise HTTPException(status_code=400, detail="Tidak bisa nonaktifkan diri sendiri.")
    target.is_active = data.is_active
    db.commit()
    db.refresh(target)
    return AdminUserItem(
        id=target.id,
        email=target.email,
        full_name=target.full_name,
        profession=target.profession,
        organization=target.organization,
        role=target.role,
        status=target.status,
        is_active=target.is_active,
        created_at=target.created_at.isoformat(),
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Tidak bisa hapus diri sendiri.")
    db.delete(target)
    db.commit()
    return {"detail": f"User {target.email} dihapus."}


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: str,
    data: ResetPasswordRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if len(data.new_password) < 8:
        raise HTTPException(status_code=422, detail="Password minimal 8 karakter.")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")
    target.password_hash = hash_password(data.new_password)
    db.commit()
    return {"detail": f"Password {target.email} direset."}


# ---------- Kelola Skill ----------


@router.get("/skills", response_model=list[SkillItem])
def list_skills(admin: User = Depends(require_admin)):
    skills = []
    if not SKILLS_DIR.is_dir():
        return skills
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        md_files = list(skill_dir.glob("*.md"))
        if not md_files:
            continue
        f = md_files[0]
        skills.append(
            SkillItem(
                name=skill_dir.name,
                instruction_file=f.name,
                chars=len(f.read_text(encoding="utf-8", errors="ignore")),
            )
        )
    return skills


@router.get("/skills/{skill_name}")
def read_skill(
    skill_name: str,
    admin: User = Depends(require_admin),
):
    if not skill_name.isalnum():
        raise HTTPException(status_code=400, detail="Nama skill tidak valid.")
    skill_dir = SKILLS_DIR / skill_name
    md_files = list(skill_dir.glob("*.md")) if skill_dir.is_dir() else []
    if not md_files:
        raise HTTPException(status_code=404, detail="Skill tidak ditemukan.")
    return {
        "name": skill_name,
        "file": md_files[0].name,
        "content": md_files[0].read_text(encoding="utf-8", errors="ignore"),
    }
