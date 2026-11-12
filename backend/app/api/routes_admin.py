"""Endpoint SuperAdmin: kelola user & skill (khusus role=admin)."""

import secrets
import uuid
from datetime import datetime
from typing import Literal
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from app.core.config import TELEGRAM_WEBHOOK_BASE_URL
from app.api.routes_telegram import decrypt_bot_token, telegram_api
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.user import User
from app.models.telegram_account import TelegramAccount
from app.services.master_agent import SKILLS_DIR
from app.services.auth_service import hash_password, verify_password
from app.services.quota_service import daily_token_usage
from app.services.token_service import _lock_user, cancel_reservation, complete_reservation

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403, detail="Akses khusus administrator."
        )
    return user


def verify_admin_password(admin: User, password: str) -> None:
    if not password or not verify_password(password, admin.password_hash):
        raise HTTPException(
            status_code=401,
            detail="Password admin salah. Tindakan dibatalkan.",
        )


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


class AdminPasswordRequest(BaseModel):
    admin_password: str


class ReservationSettlementRequest(BaseModel):
    admin_password: str


class SetRoleRequest(BaseModel):
    role: str  # "user" | "admin"
    admin_password: str


class SetActiveRequest(BaseModel):
    is_active: bool
    admin_password: str


class ResetPasswordRequest(BaseModel):
    new_password: str
    admin_password: str


class SkillItem(BaseModel):
    name: str
    instruction_file: str
    chars: int




# ---------- Monitoring Review Token ----------


@router.get("/reservations/review")
def reservation_review(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Daftar reservasi yang membutuhkan monitoring atau pemulihan."""

    rows = (
        db.query(
            TokenReservation,
            User.email,
            User.full_name,
            TokenReservationResult,
        )
        .join(User, User.id == TokenReservation.user_id)
        .outerjoin(
            TokenReservationResult,
            TokenReservationResult.reservation_id == TokenReservation.id,
        )
        .filter(
            or_(
                TokenReservation.status.in_(["pending", "needs_review"]),
                TokenReservationResult.status.in_(["received", "needs_review"]),
            )
        )
        .order_by(TokenReservation.created_at.desc())
        .limit(200)
        .all()
    )

    items = []

    for reservation, email, full_name, result in rows:
        items.append({
            "reservation_id": reservation.id,
            "user_id": reservation.user_id,
            "email": email,
            "full_name": full_name,
            "reservation_status": reservation.status,
            "result_status": result.status if result else None,
            "reserved_topup": int(reservation.reserved_topup or 0),
            "reserved_daily": int(reservation.reserved_daily or 0),
            "actual_tokens": int(reservation.actual_tokens or 0),
            "prompt_tokens": (
                int(result.prompt_tokens)
                if result and result.prompt_tokens is not None else None
            ),
            "completion_tokens": (
                int(result.completion_tokens)
                if result and result.completion_tokens is not None else None
            ),
            "total_tokens": (
                int(result.total_tokens)
                if result and result.total_tokens is not None else None
            ),
            "skill": result.skill if result else None,
            "assistant_message_id": (
                result.assistant_message_id if result else None
            ),
            "result_preview": (
                result.response_content[:300] if result else None
            ),
            "reference": reservation.reference,
            "description": reservation.description,
            "created_at": (
                reservation.created_at.isoformat()
                if reservation.created_at else None
            ),
            "expires_at": (
                reservation.expires_at.isoformat()
                if reservation.expires_at else None
            ),
            "completed_at": (
                reservation.completed_at.isoformat()
                if reservation.completed_at else None
            ),
            "result_created_at": (
                result.created_at.isoformat()
                if result and result.created_at else None
            ),
        })

    return {
        "total": len(items),
        "items": items,
        "limit": 200,
    }


@router.post("/reservations/{reservation_id}/settle")
def settle_reservation_as_admin(
    reservation_id: str,
    payload: ReservationSettlementRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Setujui settlement berdasarkan bukti usage AI yang tersimpan."""
    verify_admin_password(admin, payload.admin_password)

    reservation = db.get(TokenReservation, reservation_id)
    if reservation is None:
        raise HTTPException(status_code=404, detail="Reservasi tidak ditemukan.")

    if reservation.status not in {"pending", "needs_review", "completed"}:
        raise HTTPException(
            status_code=409,
            detail="Status reservasi tidak dapat diselesaikan melalui review.",
        )

    result = db.get(TokenReservationResult, reservation_id)
    if result is None:
        raise HTTPException(
            status_code=409,
            detail="Bukti hasil AI tidak ditemukan. Settlement ditolak.",
        )

    if result.user_id != reservation.user_id:
        raise HTTPException(
            status_code=409,
            detail="Pemilik bukti hasil AI tidak cocok dengan reservasi.",
        )

    if result.status not in {"received", "needs_review", "settled"}:
        raise HTTPException(
            status_code=409,
            detail="Status bukti hasil AI tidak dapat diselesaikan.",
        )

    values = (
        result.prompt_tokens,
        result.completion_tokens,
        result.total_tokens,
    )
    if any(value is None for value in values):
        raise HTTPException(
            status_code=409,
            detail="Data token belum lengkap. Perlu pemeriksaan manual.",
        )

    prompt_tokens, completion_tokens, actual_tokens = map(int, values)
    integer_max = 2_147_483_647

    if any(value < 0 or value > integer_max for value in values):
        raise HTTPException(
            status_code=409,
            detail="Angka token di luar batas penyimpanan database.",
        )

    if actual_tokens <= 0 or prompt_tokens + completion_tokens != actual_tokens:
        raise HTTPException(
            status_code=409,
            detail="Total token tidak cocok dengan prompt + completion.",
        )

    if not result.skill or len(result.skill) > 50:
        raise HTTPException(
            status_code=409,
            detail="Data skill hasil AI tidak valid.",
        )

    complete_reservation(
        db=db,
        reservation_id=reservation_id,
        actual_tokens=actual_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        skill=result.skill,
        allow_needs_review=True,
    )

    settled_result = db.get(TokenReservationResult, reservation_id)
    if settled_result is None:
        raise HTTPException(
            status_code=409,
            detail="Settlement tercatat, tetapi bukti hasil tidak ditemukan.",
        )

    settled_result.status = "settled"
    if settled_result.settled_at is None:
        settled_result.settled_at = datetime.utcnow()
    db.commit()

    return {
        "ok": True,
        "reservation_id": reservation_id,
        "reservation_status": "completed",
        "result_status": "settled",
        "actual_tokens": actual_tokens,
        "message": "Settlement berhasil diverifikasi dan disimpan.",
    }


@router.post("/reservations/{reservation_id}/cancel")
def cancel_reservation_as_admin(
    reservation_id: str,
    payload: AdminPasswordRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Batalkan reservasi pending/needs_review melalui persetujuan Admin."""
    verify_admin_password(admin, payload.admin_password)

    cancel_reservation(
        db=db,
        reservation_id=reservation_id,
        allow_needs_review=True,
    )

    return {
        "ok": True,
        "reservation_id": reservation_id,
        "reservation_status": "cancelled",
        "message": "Reservasi dibatalkan dan refund Top Up diproses.",
    }


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
            User.id.label("user_id"),
            User.email,
            User.full_name,
            User.role.label("role"),
            func.coalesce(func.sum(TokenUsage.prompt_tokens), 0).label("prompt"),
            func.coalesce(func.sum(TokenUsage.completion_tokens), 0).label("completion"),
            func.coalesce(func.sum(TokenUsage.total_tokens), 0).label("total"),
            func.count(TokenUsage.id).label("requests"),
            func.coalesce(TokenWallet.topup_balance, 0).label("balance"),
        )
        .outerjoin(TokenUsage, TokenUsage.user_id == User.id)
        .outerjoin(TokenWallet, TokenWallet.user_id == User.id)
        .group_by(
            User.id, User.email, User.full_name, User.role,
            TokenWallet.topup_balance
        )
        .order_by(func.sum(TokenUsage.total_tokens).desc())
        .limit(200)
        .all()
    )
    result = []
    for r in rows:
        target_user = db.get(User, r.user_id)
        daily_limit, daily_used = daily_token_usage(db, target_user)
        daily_remaining = (
            None if daily_limit is None
            else max(0, daily_limit - daily_used)
        )
        result.append(
            {
                "user_id": r.user_id,
                "email": r.email,
                "full_name": r.full_name,
                "role": r.role,
                "prompt_tokens": int(r.prompt),
                "completion_tokens": int(r.completion),
                "total_tokens": int(r.total),
                "requests": int(r.requests),
                "topup_balance": int(r.balance or 0),
                "daily_limit": daily_limit,
                "daily_used": daily_used,
                "daily_remaining": daily_remaining,
            }
        )
    return result


class AdminTokenCreditRequest(BaseModel):
    amount: int
    description: str | None = None
    admin_password: str


@router.post("/tokens/{user_id}/reset-balance")
def reset_token_balance(
    user_id: str,
    data: AdminPasswordRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Reset hanya saldo wallet pengguna; riwayat pemakaian tetap utuh."""
    verify_admin_password(admin, data.admin_password)
    target = _lock_user(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")

    try:
        wallet = (
            db.query(TokenWallet)
            .filter(TokenWallet.user_id == user_id)
            .with_for_update()
            .first()
        )

        if wallet is None:
            wallet = TokenWallet(user_id=user_id, topup_balance=0)
            db.add(wallet)
            db.flush()

        old_balance = int(wallet.topup_balance or 0)

        if old_balance == 0:
            return {
                "detail": "Saldo pengguna sudah 0.",
                "user_id": user_id,
                "previous_balance": 0,
                "topup_balance": 0,
            }

        wallet.topup_balance = 0

        db.add(
            TokenTransaction(
                id=str(uuid.uuid4()),
                user_id=user_id,
                transaction_type="admin_reset",
                amount=-old_balance,
                balance_after=0,
                reference=f"admin-reset:{uuid.uuid4()}",
                description=(
                    f"Saldo token direset oleh admin {admin.email}. "
                    f"Saldo sebelumnya: {old_balance} token."
                ),
            )
        )

        db.commit()
        return {
            "detail": f"Saldo token {target.email} berhasil direset.",
            "user_id": user_id,
            "previous_balance": old_balance,
            "topup_balance": 0,
        }

    except Exception:
        db.rollback()
        raise


@router.post("/tokens/{user_id}/add-balance")
def add_token_balance(
    user_id: str,
    data: AdminTokenCreditRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Menambahkan saldo token secara manual oleh admin."""
    verify_admin_password(admin, data.admin_password)
    if data.amount <= 0 or data.amount > 9_000_000_000_000_000:
        raise HTTPException(
            status_code=422,
            detail="Jumlah token harus bilangan bulat positif yang valid.",
        )

    target = _lock_user(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User tidak ditemukan.")

    try:
        wallet = (
            db.query(TokenWallet)
            .filter(TokenWallet.user_id == user_id)
            .with_for_update()
            .first()
        )

        if wallet is None:
            wallet = TokenWallet(user_id=user_id, topup_balance=0)
            db.add(wallet)
            db.flush()

        old_balance = int(wallet.topup_balance or 0)
        new_balance = old_balance + data.amount

        if new_balance > 9_000_000_000_000_000:
            raise HTTPException(
                status_code=422,
                detail="Saldo setelah penambahan melebihi batas yang diizinkan.",
            )

        wallet.topup_balance = new_balance

        note = (data.description or "").strip()
        description = (
            f"Penambahan saldo manual oleh admin {admin.email}. "
            f"Jumlah: {data.amount} token."
        )
        if note:
            description += f" Catatan: {note[:500]}"

        db.add(
            TokenTransaction(
                id=str(uuid.uuid4()),
                user_id=user_id,
                transaction_type="admin_topup",
                amount=data.amount,
                balance_after=new_balance,
                reference=f"admin-manual:{uuid.uuid4()}",
                description=description,
            )
        )

        db.commit()
        return {
            "detail": f"Berhasil menambahkan {data.amount:,} token untuk {target.email}.",
            "user_id": user_id,
            "previous_balance": old_balance,
            "added_tokens": data.amount,
            "topup_balance": new_balance,
        }

    except Exception:
        db.rollback()
        raise


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
    verify_admin_password(admin, data.admin_password)
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
    verify_admin_password(admin, data.admin_password)
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
    data: AdminPasswordRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    verify_admin_password(admin, data.admin_password)
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
    verify_admin_password(admin, data.admin_password)
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


# ---------- Persetujuan koneksi Telegram ----------

class TelegramDecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


class TelegramStatusRequest(BaseModel):
    is_active: bool
    admin_password: str


class TelegramDisconnectRequest(BaseModel):
    admin_password: str


@router.get("/telegram/pending")
def list_pending_telegram(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Daftar bot Telegram yang menunggu persetujuan admin."""
    rows = (
        db.query(TelegramAccount, User)
        .join(User, TelegramAccount.user_id == User.id)
        .filter(TelegramAccount.status == "pending")
        .order_by(TelegramAccount.created_at.asc())
        .all()
    )

    return [
        {
            "id": account.id,
            "user_id": account.user_id,
            "bot_username": account.bot_username,
            "status": account.status,
            "created_at": account.created_at.isoformat(),
            "user_name": user.full_name or "",
            "user_email": user.email,
        }
        for account, user in rows
    ]


@router.post("/telegram/{account_id}/decision")
def decide_telegram(
    account_id: str,
    data: TelegramDecisionRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Setujui atau tolak permintaan koneksi bot Telegram."""
    account = db.get(TelegramAccount, account_id)

    if account is None:
        raise HTTPException(
            status_code=404,
            detail="Permintaan koneksi Telegram tidak ditemukan.",
        )

    if account.status != "pending":
        raise HTTPException(
            status_code=409,
            detail="Permintaan Telegram sudah diproses sebelumnya.",
        )

    account.status = (
        "approved" if data.decision == "approve" else "rejected"
    )

    db.commit()
    db.refresh(account)

    return {
        "id": account.id,
        "bot_username": account.bot_username,
        "status": account.status,
        "message": (
            "Koneksi bot disetujui. Aktivasi webhook dan linking "
            "masih merupakan tahap teknis terpisah."
            if data.decision == "approve"
            else "Permintaan koneksi bot ditolak."
        ),
    }


# ---------- Pengelolaan Telegram Tertaut ----------

@router.get("/telegram/linked")
def list_linked_telegram(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Daftar akun Telegram yang sudah tertaut ke pemiliknya."""
    rows = (
        db.query(TelegramAccount, User)
        .join(User, TelegramAccount.user_id == User.id)
        .filter(TelegramAccount.telegram_owner_id.isnot(None))
        .order_by(TelegramAccount.created_at.desc())
        .all()
    )

    return [
        {
            "id": account.id,
            "user_id": account.user_id,
            "user_email": user.email,
            "user_name": user.full_name or "",
            "telegram_username": account.telegram_owner_username,
            "telegram_id": str(account.telegram_owner_id),
            "bot_username": account.bot_username,
            "status": account.status,
            "created_at": account.created_at.isoformat(),
        }
        for account, user in rows
    ]


@router.patch("/telegram/{account_id}/status")
def set_linked_telegram_status(
    account_id: str,
    data: TelegramStatusRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Aktifkan atau nonaktifkan webhook akun Telegram tertaut."""
    verify_admin_password(admin, data.admin_password)

    account = db.get(TelegramAccount, account_id)
    if account is None or account.telegram_owner_id is None:
        raise HTTPException(
            status_code=404,
            detail="Akun Telegram tertaut tidak ditemukan.",
        )

    if data.is_active:
        if account.status == "active":
            raise HTTPException(
                status_code=409,
                detail="Akun Telegram sudah aktif.",
            )
        if account.status != "disabled":
            raise HTTPException(
                status_code=409,
                detail="Hanya akun Telegram nonaktif yang dapat diaktifkan kembali.",
            )

        token = decrypt_bot_token(account)
        webhook_url = (
            f"{TELEGRAM_WEBHOOK_BASE_URL}/api/v1/telegram/webhook/{account.id}"
        )

        telegram_api(
            token,
            "setWebhook",
            url=webhook_url,
            secret_token=account.webhook_secret,
            allowed_updates=["message"],
            drop_pending_updates=False,
        )

        account.status = "active"
        account.last_error = None
        db.commit()

        return {"detail": "Koneksi Telegram berhasil diaktifkan."}

    if account.status == "disabled":
        raise HTTPException(
            status_code=409,
            detail="Akun Telegram sudah nonaktif.",
        )
    if account.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Status akun Telegram tidak dapat dinonaktifkan dari kondisi ini.",
        )

    token = decrypt_bot_token(account)

    # Hapus webhook terlebih dahulu. Jika gagal, status lokal tidak berubah.
    telegram_api(
        token,
        "deleteWebhook",
        drop_pending_updates=False,
    )

    account.status = "disabled"
    account.link_code_hash = None
    account.link_code_expires_at = None
    db.commit()

    return {"detail": "Koneksi Telegram berhasil dinonaktifkan."}


@router.delete("/telegram/{account_id}")
def disconnect_linked_telegram(
    account_id: str,
    data: TelegramDisconnectRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Putuskan koneksi Telegram tanpa menghapus akun atau percakapan pengguna."""
    verify_admin_password(admin, data.admin_password)

    account = db.get(TelegramAccount, account_id)
    if account is None:
        raise HTTPException(
            status_code=404,
            detail="Koneksi Telegram tidak ditemukan.",
        )

    if account.telegram_owner_id is None:
        raise HTTPException(
            status_code=409,
            detail="Akun Telegram ini belum tertaut ke pemilik.",
        )

    token = decrypt_bot_token(account)

    # Jangan hapus data lokal sebelum Telegram mengonfirmasi pelepasan webhook.
    telegram_api(
        token,
        "deleteWebhook",
        drop_pending_updates=False,
    )

    db.delete(account)
    db.commit()

    return {"detail": "Koneksi Telegram berhasil diputuskan."}

