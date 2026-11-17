"""Endpoint Top Up token melalui QRIS dengan verifikasi manual admin."""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.database import get_db
from app.models.subscription import (
    SubscriptionPlan,
    TokenTransaction,
    UserSubscription,
)
from app.models.topup import TopUpRequest
from app.models.user import User
from app.services.document_service import STORAGE_DIR
from app.services.topup_notification import notify_admin_new_topup
from app.services.token_service import (
    _get_wallet,
    _lock_user,
    utc_now,
    wit_now,
)


router = APIRouter(prefix="/api/v1/topup", tags=["topup"])

PROOF_DIR = STORAGE_DIR / "topup_proofs"
PACKAGE_QRIS_DIR = STORAGE_DIR / "package_qris"
MAX_PROOF_SIZE = 5 * 1024 * 1024
MAX_QRIS_SIZE = 5 * 1024 * 1024
ALLOWED_QRIS = {
    b"\x89PNG\r\n\x1a\n": (".png", "image/png"),
    b"\xff\xd8\xff": (".jpg", "image/jpeg"),
}

PLAN_CODE_ALIASES = {
    "basic": "starter",
    "pro": "regular",
    "premium": "professional",
}


def active_subscriber_count(db: Session, plan: SubscriptionPlan) -> int:
    """Hitung pengguna dengan langganan aktif yang masih dalam periode."""
    now_wit = wit_now()

    count = (
        db.query(func.count(func.distinct(UserSubscription.user_id)))
        .filter(
            UserSubscription.plan_id == plan.id,
            UserSubscription.status == "active",
            UserSubscription.current_period_start <= now_wit,
            UserSubscription.current_period_end > now_wit,
        )
        .scalar()
    )

    return int(count or 0)

ALLOWED_PROOFS = {
    b"\x89PNG\r\n\x1a\n": ".png",
    b"\xff\xd8\xff": ".jpg",
    b"%PDF-": ".pdf",
}


def require_topup_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Akses khusus administrator.",
        )
    return user


def request_data(item: TopUpRequest) -> dict:
    return {
        "id": item.id,
        "package_code": item.package_code,
        "amount_idr": int(item.amount_idr),
        "token_amount": int(item.token_amount),
        "payment_method": item.payment_method,
        "status": item.status,
        "admin_note": item.admin_note or "",
        "created_at": (
            item.created_at.isoformat() if item.created_at else None
        ),
        "reviewed_at": (
            item.reviewed_at.isoformat() if item.reviewed_at else None
        ),
    }


class ReviewRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    admin_note: str = Field(default="", max_length=1000)



class PlanCreateRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="")
    price_idr: int = Field(gt=0)
    token_amount: int = Field(gt=0)
    daily_token_limit: int = Field(gt=0)
    duration_days: int = Field(ge=1, le=365)
    max_users: int | None = Field(default=10, ge=1)
    sort_order: int = Field(default=0, ge=0)
    is_active: bool = True


class PlanUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    price_idr: int | None = Field(default=None, gt=0)
    token_amount: int | None = Field(default=None, gt=0)
    daily_token_limit: int | None = Field(default=None, gt=0)
    duration_days: int | None = Field(default=None, ge=1, le=365)
    max_users: int | None = Field(default=None, ge=1)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


def admin_plan_data(db: Session, plan: SubscriptionPlan) -> dict:
    active_users = active_subscriber_count(db, plan)
    max_users = (
        int(plan.max_users) if plan.max_users is not None else None
    )

    return {
        "id": plan.id,
        "code": plan.code,
        "name": plan.name,
        "description": plan.description or "",
        "price_idr": int(plan.price_idr),
        "token_amount": int(plan.token_amount),
        "daily_token_limit": int(plan.daily_token_limit),
        "duration_days": int(plan.duration_days),
        "max_users": max_users,
        "active_users": active_users,
        "available_slots": (
            max(0, max_users - active_users)
            if max_users is not None else None
        ),
        "is_full": (
            max_users is not None and active_users >= max_users
        ),
        "sort_order": int(plan.sort_order),
        "is_active": bool(plan.is_active),
        "qris_image": plan.qris_image,
        "qris_image_url": (
            plan.qris_image if plan.qris_image and plan.qris_image.startswith("/qris/")
            else f"/api/v1/topup/packages/{plan.code}/qris"
            if plan.qris_image else None
        ),
        "has_qris": bool(plan.qris_image),
    }


@router.get("/admin/packages")
def get_admin_packages(
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    plans = (
        db.query(SubscriptionPlan)
        .order_by(
            SubscriptionPlan.sort_order.asc(),
            SubscriptionPlan.name.asc(),
        )
        .all()
    )

    return {
        "packages": [
            admin_plan_data(db, plan)
            for plan in plans
        ]
    }


@router.post("/admin/packages")
def create_admin_package(
    body: PlanCreateRequest,
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    existing = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.code == body.code)
        .first()
    )

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Kode paket sudah digunakan.",
        )

    plan = SubscriptionPlan(**body.model_dump())

    try:
        db.add(plan)
        db.commit()
        db.refresh(plan)

        return {
            "message": "Paket berhasil ditambahkan.",
            "package": admin_plan_data(db, plan),
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Kode paket sudah digunakan.",
        )
    except Exception:
        db.rollback()
        raise


@router.patch("/admin/packages/{code}")
def update_admin_package(
    code: str,
    body: PlanUpdateRequest,
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.code == code)
        .with_for_update()
        .first()
    )

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail="Paket tidak ditemukan.",
        )

    changes = body.model_dump(exclude_unset=True)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail="Tidak ada perubahan paket yang dikirim.",
        )

    for field, value in changes.items():
        if field != "max_users" and value is None:
            raise HTTPException(
                status_code=422,
                detail=f"Nilai {field} tidak boleh kosong.",
            )

    for field, value in changes.items():
        setattr(plan, field, value)

    try:
        db.commit()
        db.refresh(plan)

        return {
            "message": "Paket berhasil diperbarui.",
            "package": admin_plan_data(db, plan),
        }
    except Exception:
        db.rollback()
        raise


@router.post("/admin/packages/{code}/qris")
async def upload_package_qris(
    code: str,
    image: UploadFile = File(...),
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    plan = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.code == code)
        .with_for_update()
        .first()
    )

    if plan is None:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan.")

    content = await image.read(MAX_QRIS_SIZE + 1)

    try:
        if not content:
            raise HTTPException(status_code=400, detail="File QRIS kosong.")

        if len(content) > MAX_QRIS_SIZE:
            raise HTTPException(
                status_code=413,
                detail="Ukuran gambar QRIS maksimal 5 MB.",
            )

        detected = next(
            (
                details
                for signature, details in ALLOWED_QRIS.items()
                if content.startswith(signature)
            ),
            None,
        )

        if detected is None:
            raise HTTPException(
                status_code=400,
                detail="Format QRIS harus berupa gambar PNG atau JPG yang valid.",
            )

        extension, media_type = detected
        PACKAGE_QRIS_DIR.mkdir(parents=True, exist_ok=True)

        filename = f"{uuid.uuid4().hex}{extension}"
        destination = PACKAGE_QRIS_DIR / filename

        try:
            destination.write_bytes(content)
            plan.qris_image = filename
            db.commit()
            db.refresh(plan)
        except Exception:
            db.rollback()
            destination.unlink(missing_ok=True)
            raise

        return {
            "message": "QRIS paket berhasil diunggah.",
            "package": admin_plan_data(db, plan),
        }
    finally:
        await image.close()


@router.get("/packages/{code}/qris")
def get_package_qris(
    code: str,
    db: Session = Depends(get_db),
):
    plan = (
        db.query(SubscriptionPlan)
        .filter(
            SubscriptionPlan.code == code,
            SubscriptionPlan.is_active.is_(True),
        )
        .first()
    )

    if plan is None or not plan.qris_image:
        raise HTTPException(status_code=404, detail="QRIS paket tidak tersedia.")

    filename = plan.qris_image

    # QRIS lama menggunakan file statis frontend /qris/.
    if filename.startswith("/qris/") or Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="QRIS paket tidak tersedia.")

    qris_path = PACKAGE_QRIS_DIR / filename

    if not qris_path.is_file() or qris_path.parent != PACKAGE_QRIS_DIR:
        raise HTTPException(status_code=404, detail="File QRIS tidak ditemukan.")

    media_type = (
        "image/png" if qris_path.suffix.lower() == ".png"
        else "image/jpeg" if qris_path.suffix.lower() in {".jpg", ".jpeg"}
        else "application/octet-stream"
    )

    return FileResponse(path=qris_path, media_type=media_type)


@router.get("/packages")
def get_packages(db: Session = Depends(get_db)):
    plans = (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active.is_(True))
        .order_by(SubscriptionPlan.sort_order.asc(), SubscriptionPlan.name.asc())
        .all()
    )

    packages = []

    for plan in plans:
        active_users = active_subscriber_count(db, plan)
        max_users = (
            int(plan.max_users) if plan.max_users is not None else None
        )
        is_full = (
            max_users is not None and active_users >= max_users
        )

        packages.append({
            "code": plan.code,
            "name": plan.name,
            "amount_idr": int(plan.price_idr),
            "token_amount": int(plan.token_amount),
            "daily_token_limit": int(plan.daily_token_limit),
            "duration_days": int(plan.duration_days),
            "max_users": max_users,
            "active_users": active_users,
            "available_slots": (
                max(0, max_users - active_users)
                if max_users is not None else None
            ),
            "is_full": is_full,
            "qris_image_url": (
                plan.qris_image if plan.qris_image and plan.qris_image.startswith("/qris/")
                else f"/api/v1/topup/packages/{plan.code}/qris"
                if plan.qris_image else None
            ),
            "has_qris": bool(plan.qris_image),
        })

    return {"packages": packages}


@router.post("/requests")
async def create_topup_request(
    package_code: str = Form(...),
    proof: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    package_code = package_code.strip().lower()
    canonical_package_code = PLAN_CODE_ALIASES.get(
        package_code, package_code
    )

    plan = (
        db.query(SubscriptionPlan)
        .filter(
            SubscriptionPlan.code == canonical_package_code,
            SubscriptionPlan.is_active.is_(True),
        )
        .first()
    )

    if plan is None:
        raise HTTPException(
            status_code=400,
            detail="Paket Top Up tidak tersedia atau sudah dinonaktifkan.",
        )

    if not plan.qris_image:
        raise HTTPException(
            status_code=400,
            detail="QRIS untuk paket ini belum tersedia.",
        )

    active_users = active_subscriber_count(db, plan)

    if (
        plan.max_users is not None
        and active_users >= int(plan.max_users)
    ):
        raise HTTPException(
            status_code=409,
            detail="Kapasitas paket sudah penuh. Silakan pilih paket lain.",
        )

    content = await proof.read(MAX_PROOF_SIZE + 1)
    if not content:
        raise HTTPException(
            status_code=400,
            detail="File bukti pembayaran kosong.",
        )

    if len(content) > MAX_PROOF_SIZE:
        raise HTTPException(
            status_code=413,
            detail="Ukuran bukti pembayaran maksimal 5 MB.",
        )

    extension = next(
        (
            ext
            for signature, ext in ALLOWED_PROOFS.items()
            if content.startswith(signature)
        ),
        None,
    )

    if extension is None:
        raise HTTPException(
            status_code=400,
            detail="Format bukti harus JPG, PNG, atau PDF yang valid.",
        )

    user_dir = PROOF_DIR / str(user.id)
    user_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex}{extension}"
    destination = user_dir / filename

    try:
        destination.write_bytes(content)

        item = TopUpRequest(
            id=str(uuid.uuid4()),
            user_id=user.id,
            package_code=canonical_package_code,
            amount_idr=int(plan.price_idr),
            token_amount=int(plan.token_amount),
            payment_method="qris",
            proof_file=filename,
            status="pending",
        )

        db.add(item)
        db.commit()
        db.refresh(item)

        try:
            await notify_admin_new_topup(user, plan, item)
        except Exception:
            # Kegagalan Telegram tidak boleh membatalkan pengajuan Top Up.
            pass

        return {
            "message": "Pengajuan Top Up berhasil dikirim dan menunggu verifikasi admin.",
            "request": request_data(item),
        }

    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise

    finally:
        await proof.close()


@router.get("/requests")
def get_my_topup_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = (
        db.query(TopUpRequest)
        .filter(TopUpRequest.user_id == user.id)
        .order_by(TopUpRequest.created_at.desc())
        .limit(100)
        .all()
    )

    return {"requests": [request_data(item) for item in items]}


@router.get("/admin/requests")
def get_admin_topup_requests(
    status_filter: Literal["pending", "approved", "rejected", "all"] = "pending",
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    query = (
        db.query(TopUpRequest, User)
        .join(User, User.id == TopUpRequest.user_id)
    )

    if status_filter != "all":
        query = query.filter(TopUpRequest.status == status_filter)

    rows = (
        query.order_by(TopUpRequest.created_at.desc())
        .limit(200)
        .all()
    )

    return {
        "requests": [
            {
                **request_data(item),
                "user_id": item.user_id,
                "user_name": account.full_name,
                "user_email": account.email,
            }
            for item, account in rows
        ]
    }


@router.get("/admin/requests/{request_id}/proof")
def get_admin_topup_proof(
    request_id: str,
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    item = (
        db.query(TopUpRequest)
        .filter(TopUpRequest.id == request_id)
        .first()
    )

    if item is None:
        raise HTTPException(status_code=404, detail="Pengajuan tidak ditemukan.")

    if Path(item.proof_file).name != item.proof_file:
        raise HTTPException(status_code=404, detail="Bukti pembayaran tidak ditemukan.")

    user_dir = PROOF_DIR / str(item.user_id)
    proof_path = user_dir / item.proof_file

    if not proof_path.is_file() or proof_path.parent != user_dir:
        raise HTTPException(status_code=404, detail="Bukti pembayaran tidak ditemukan.")

    media_types = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".pdf": "application/pdf",
    }

    return FileResponse(
        path=proof_path,
        media_type=media_types.get(proof_path.suffix.lower(), "application/octet-stream"),
        filename=proof_path.name,
    )


@router.post("/admin/requests/{request_id}/review")
def review_topup_request(
    request_id: str,
    body: ReviewRequest,
    admin: User = Depends(require_topup_admin),
    db: Session = Depends(get_db),
):
    try:
        # Urutan lock: User -> Wallet -> TopUpRequest -> SubscriptionPlan.
        initial = (
            db.query(TopUpRequest.user_id)
            .filter(TopUpRequest.id == request_id)
            .first()
        )

        if initial is None:
            raise HTTPException(
                status_code=404,
                detail="Pengajuan Top Up tidak ditemukan.",
            )

        user_id = initial[0]

        if _lock_user(db, user_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Pengguna tidak ditemukan.",
            )

        wallet = _get_wallet(db, user_id)

        item = (
            db.query(TopUpRequest)
            .filter(TopUpRequest.id == request_id)
            .with_for_update()
            .first()
        )

        if item is None:
            raise HTTPException(
                status_code=404,
                detail="Pengajuan Top Up tidak ditemukan.",
            )

        if item.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=f"Pengajuan sudah diproses dengan status {item.status}.",
            )

        subscription_changed = False
        subscription_note = ""

        if body.decision == "approved":
            legacy_code = item.package_code.strip().lower()
            canonical_code = PLAN_CODE_ALIASES.get(
                legacy_code, legacy_code
            )

            # Ambil dan kunci master paket. Tidak membuat atau menimpa
            # konfigurasi paket dari nilai hardcoded.
            plan = (
                db.query(SubscriptionPlan)
                .filter(SubscriptionPlan.code == canonical_code)
                .with_for_update()
                .first()
            )

            if plan is None:
                raise HTTPException(
                    status_code=409,
                    detail="Paket pada pengajuan tidak ditemukan.",
                )

            if not plan.is_active:
                raise HTTPException(
                    status_code=409,
                    detail="Paket sudah dinonaktifkan. Pengajuan belum dapat disetujui.",
                )

            now_wit = wit_now()

            active_rows = (
                db.query(UserSubscription)
                .filter(
                    UserSubscription.user_id == user_id,
                    UserSubscription.status == "active",
                )
                .order_by(UserSubscription.current_period_start.desc())
                .all()
            )

            current_subscription = next(
                (
                    row for row in active_rows
                    if row.current_period_start <= now_wit
                    and row.current_period_end > now_wit
                ),
                None,
            )

            should_activate = current_subscription is None

            if current_subscription is not None:
                current_plan = (
                    db.query(SubscriptionPlan)
                    .filter(
                        SubscriptionPlan.id == current_subscription.plan_id
                    )
                    .first()
                )

                # Paket lama yang tidak dikenal tidak diturunkan.
                current_order = (
                    int(current_plan.sort_order)
                    if current_plan is not None else 99
                )

                if int(plan.sort_order) > current_order:
                    should_activate = True

            # Kapasitas diperiksa ulang saat persetujuan, di bawah lock
            # master paket agar persetujuan bersamaan tidak oversubscribe.
            if should_activate and plan.max_users is not None:
                active_count = active_subscriber_count(db, plan)

                if active_count >= int(plan.max_users):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "Kapasitas paket sudah penuh. "
                            "Pengajuan tetap pending dan saldo tidak berubah."
                        ),
                    )

            # Gunakan snapshot transaksi, bukan nilai paket terbaru.
            credited_tokens = int(item.token_amount)
            wallet.topup_balance += credited_tokens

            db.add(
                TokenTransaction(
                    id=str(uuid.uuid4()),
                    user_id=user_id,
                    transaction_type="topup",
                    amount=credited_tokens,
                    balance_after=int(wallet.topup_balance),
                    reference=f"topup:{item.id}",
                    description=(
                        f"Top Up QRIS disetujui: "
                        f"{item.package_code}, "
                        f"Rp{int(item.amount_idr):,}."
                    ),
                )
            )

            if should_activate:
                for old_subscription in active_rows:
                    old_subscription.status = (
                        "expired"
                        if old_subscription.current_period_end <= now_wit
                        else "replaced"
                    )

                db.add(
                    UserSubscription(
                        user_id=user_id,
                        plan_id=plan.id,
                        status="active",
                        daily_token_limit=int(plan.daily_token_limit),
                        current_period_start=now_wit,
                        current_period_end=(
                            now_wit + timedelta(days=int(plan.duration_days))
                        ),
                    )
                )

                subscription_changed = True
                subscription_note = (
                    f"Paket {plan.name} aktif selama "
                    f"{int(plan.duration_days)} hari."
                )

            elif current_subscription is not None:
                subscription_note = (
                    "Saldo Top Up ditambahkan. Paket aktif dipertahankan "
                    "karena paket yang dibeli sama atau lebih rendah."
                )

        item.status = body.decision
        item.admin_note = body.admin_note.strip()
        item.reviewed_by = admin.id
        item.reviewed_at = utc_now()

        db.commit()
        db.refresh(item)

        return {
            "message": (
                "Top Up disetujui dan saldo berhasil ditambahkan. "
                + subscription_note
                if body.decision == "approved"
                else "Pengajuan Top Up ditolak."
            ),
            "subscription_changed": subscription_changed,
            "request": request_data(item),
        }

    except Exception:
        db.rollback()
        raise
