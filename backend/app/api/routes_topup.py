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
from app.services.token_service import (
    _get_wallet,
    _lock_user,
    utc_now,
    wit_now,
)


router = APIRouter(prefix="/api/v1/topup", tags=["topup"])

PROOF_DIR = STORAGE_DIR / "topup_proofs"
MAX_PROOF_SIZE = 5 * 1024 * 1024

PACKAGES = {
    "starter": {
        "name": "Starter",
        "amount_idr": 10000,
        "token_amount": 100000,
        "duration_days": 7,
        "daily_token_limit": 50000,
        "rank": 1,
    },
    "regular": {
        "name": "Reguler",
        "amount_idr": 25000,
        "token_amount": 250000,
        "duration_days": 14,
        "daily_token_limit": 50000,
        "rank": 2,
    },
    "professional": {
        "name": "Profesional",
        "amount_idr": 50000,
        "token_amount": 550000,
        "duration_days": 21,
        "daily_token_limit": 150000,
        "rank": 3,
    },
    "business": {
        "name": "Bisnis",
        "amount_idr": 100000,
        "token_amount": 1100000,
        "duration_days": 30,
        "daily_token_limit": 150000,
        "rank": 4,
    },
}

LEGACY_PLAN_CODE_ALIASES = {
    "basic": "starter",
    "pro": "regular",
    "premium": "professional",
}

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


@router.get("/packages")
def get_packages():
    return {
        "packages": [
            {"code": code, **package}
            for code, package in PACKAGES.items()
        ]
    }


@router.post("/requests")
async def create_topup_request(
    package_code: str = Form(...),
    proof: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    package_code = package_code.strip().lower()
    package = PACKAGES.get(package_code)
    if package is None:
        raise HTTPException(
            status_code=400,
            detail="Paket Top Up tidak valid.",
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
            package_code=package_code,
            amount_idr=package["amount_idr"],
            token_amount=package["token_amount"],
            payment_method="qris",
            proof_file=filename,
            status="pending",
        )

        db.add(item)
        db.commit()
        db.refresh(item)

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
        # Baca user_id untuk menentukan urutan penguncian.
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

        # Urutan lock wajib: User -> TokenWallet -> TopUpRequest.
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

            legacy_package_code = item.package_code.strip().lower()
            canonical_package_code = LEGACY_PLAN_CODE_ALIASES.get(
                legacy_package_code, legacy_package_code
            )
            package = PACKAGES.get(canonical_package_code)
            if package is None:
                raise HTTPException(
                    status_code=409,
                    detail="Paket pada pengajuan tidak dikenali.",
                )

            # Buat atau perbarui master paket dalam transaksi yang sama.
            plan = (
                db.query(SubscriptionPlan)
                .filter(
                    SubscriptionPlan.code == canonical_package_code
                )
                .with_for_update()
                .first()
            )

            if plan is None:
                plan = SubscriptionPlan(
                    code=canonical_package_code,
                    name=package["name"],
                    description=f"Paket {package['name']} ONE MASTER AI",
                    price_idr=int(package["amount_idr"]),
                    daily_token_limit=int(package["daily_token_limit"]),
                    duration_days=int(package["duration_days"]),
                    is_active=True,
                )
                db.add(plan)
                db.flush()
            else:
                plan.name = package["name"]
                plan.price_idr = int(package["amount_idr"])
                plan.daily_token_limit = int(package["daily_token_limit"])
                plan.duration_days = int(package["duration_days"])
                plan.is_active = True

            now_wit = wit_now()

            # User sudah dikunci di awal transaksi. Semua perubahan
            # langganan pengguna ini diserialisasi melalui lock tersebut.
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

                # Paket lama yang tidak dikenal dipertahankan secara aman:
                # pembelian tetap menambah saldo, tetapi tidak menurunkan paket.
                current_rank = (
                    PACKAGES.get(
                        LEGACY_PLAN_CODE_ALIASES.get(
                            current_plan.code, current_plan.code
                        ),
                        {},
                    ).get("rank", 99)
                    if current_plan else 99
                )

                if int(package["rank"]) > int(current_rank):
                    should_activate = True

            if should_activate:
                # Tandai langganan lama agar tidak ada dua paket aktif.
                for old_subscription in active_rows:
                    old_subscription.status = (
                        "expired"
                        if old_subscription.current_period_end <= now_wit
                        else "replaced"
                    )

                new_subscription = UserSubscription(
                    user_id=user_id,
                    plan_id=plan.id,
                    status="active",
                    # Saldo token paket tidak digunakan oleh token_service.
                    # Semua token tetap berada pada wallet Top Up.
                    daily_token_limit=int(package["daily_token_limit"]),
                    current_period_start=now_wit,
                    current_period_end=(
                        now_wit + timedelta(days=int(package["duration_days"]))
                    ),
                )
                db.add(new_subscription)
                subscription_changed = True
                subscription_note = (
                    f"Paket {package['name']} aktif selama "
                    f"{package['duration_days']} hari."
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
