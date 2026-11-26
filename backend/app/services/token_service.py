"""Layanan terpusat untuk reservasi dan pemakaian token ONE MASTER AI."""

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.quota_service import daily_token_usage

WIT = ZoneInfo("Asia/Jayapura")
RESERVATION_TIMEOUT_MINUTES = 10


def wit_now() -> datetime:
    """Waktu WIT tanpa informasi timezone untuk kolom periode langganan."""
    return datetime.now(WIT).replace(tzinfo=None)


def utc_now() -> datetime:
    """Waktu UTC-naive untuk kolom timestamp database."""
    return datetime.now(timezone.utc).replace(tzinfo=None)



def _lock_user(db: Session, user_id: str) -> User | None:
    """Kunci baris pengguna untuk menyerialisasi transaksi token per user."""
    return (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .first()
    )


def _get_wallet(db: Session, user_id: str) -> TokenWallet:
    wallet = (
        db.query(TokenWallet)
        .filter(TokenWallet.user_id == user_id)
        .with_for_update()
        .first()
    )

    if wallet is None:
        db.execute(
            pg_insert(TokenWallet)
            .values(user_id=user_id, topup_balance=0)
            .on_conflict_do_nothing(index_elements=[TokenWallet.user_id])
        )
        wallet = (
            db.query(TokenWallet)
            .filter(TokenWallet.user_id == user_id)
            .with_for_update()
            .first()
        )

    if wallet is None:
        raise RuntimeError("Wallet pengguna gagal dibuat atau ditemukan.")

    return wallet



def reserve_tokens(
    db: Session,
    user: User,
    estimated_tokens: int,
    reference: str,
    description: str = "",
    *,
    estimated_prompt_tokens: int | None = None,
    requested_output_tokens: int = 4096,
) -> TokenReservation | None:
    """
    Reservasi kapasitas harian dan saldo Top Up.

    max_output_tokens disimpan sementara pada objek reservasi untuk
    membatasi output provider tanpa menambah kolom database.
    Pemanggil lama tetap dapat menggunakan signature sebelumnya.
    """

    if user.role == "admin":
        return None

    if user.status != "active" or not user.is_active:
        raise HTTPException(status_code=403, detail="Akun belum aktif.")

    try:
        estimated_tokens = int(estimated_tokens)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Estimasi token tidak valid.")

    if estimated_tokens <= 0:
        raise HTTPException(
            status_code=400,
            detail="Estimasi token harus lebih dari nol.",
        )

    try:
        requested_output_tokens = int(requested_output_tokens)
        if estimated_prompt_tokens is not None:
            estimated_prompt_tokens = int(estimated_prompt_tokens)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Estimasi prompt atau batas output tidak valid.",
        )

    if requested_output_tokens <= 0:
        raise HTTPException(
            status_code=400,
            detail="Batas output token harus lebih dari nol.",
        )

    if (
        estimated_prompt_tokens is not None
        and estimated_prompt_tokens < 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Estimasi prompt tidak valid.",
        )

    now_utc = utc_now()

    try:
        locked_user = _lock_user(db, user.id)
        if locked_user is None:
            raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan.")

        db.refresh(locked_user)

        if locked_user.status != "active" or not locked_user.is_active:
            raise HTTPException(status_code=403, detail="Akun belum aktif.")

        # Kunci wallet terlebih dahulu agar pemeriksaan saldo dan kuota
        # dilakukan terhadap saldo yang sama selama reservasi.
        wallet = _get_wallet(db, locked_user.id)
        topup_available = int(wallet.topup_balance or 0)

        if topup_available < 0:
            debt = abs(topup_available)
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Saldo Top Up Anda negatif (utang {debt} token). "
                    "Silakan Top Up atau minta Admin menambahkan saldo "
                    "sebelum menggunakan AI kembali."
                ),
            )

        daily_limit, daily_used = daily_token_usage(db, locked_user)

        if daily_limit is None:
            raise RuntimeError("Batas kuota pengguna tidak valid.")

        daily_remaining = max(0, daily_limit - daily_used)

        # Mode baru: prompt dan output dihitung terpisah agar output
        # provider tidak melampaui kapasitas harian maupun saldo Top Up.
        if estimated_prompt_tokens is not None:
            prompt_reserve = estimated_prompt_tokens

            daily_output_capacity = daily_remaining - prompt_reserve
            wallet_output_capacity = topup_available - prompt_reserve

            if daily_output_capacity <= 0:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Sisa batas harian tidak mencukupi untuk estimasi "
                        f"prompt {prompt_reserve} token. Sisa batas: "
                        f"{daily_remaining} token."
                    ),
                )

            if wallet_output_capacity <= 0:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Saldo Top Up tidak mencukupi untuk estimasi prompt "
                        f"{prompt_reserve} token. Saldo tersedia: "
                        f"{max(0, topup_available)} token."
                    ),
                )

            allowed_output_tokens = min(
                requested_output_tokens,
                daily_output_capacity,
                wallet_output_capacity,
            )
            reserved_daily = prompt_reserve + allowed_output_tokens
            reserved_topup = reserved_daily

        else:
            # Kompatibilitas untuk pemanggil lama yang hanya memberikan
            # estimasi total tanpa rincian prompt dan output.
            if estimated_tokens > daily_remaining:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Batas pemakaian token harian tidak mencukupi. "
                        f"Sisa batas hari ini: {daily_remaining} token; "
                        f"estimasi permintaan: {estimated_tokens} token."
                    ),
                )

            if topup_available < estimated_tokens:
                shortage = estimated_tokens - max(0, topup_available)
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Saldo Top Up tidak mencukupi. "
                        f"Saldo tersedia: {max(0, topup_available)} token; "
                        f"Kekurangan: {shortage} token."
                    ),
                )

            allowed_output_tokens = min(
                requested_output_tokens,
                estimated_tokens,
            )
            reserved_daily = estimated_tokens
            reserved_topup = estimated_tokens

        # Batas harian hanya membatasi pemakaian.
        # Seluruh token tetap berasal dari saldo Top Up.
        wallet.topup_balance -= reserved_topup

        reservation = TokenReservation(
            id=str(uuid.uuid4()),
            user_id=locked_user.id,
            subscription_id=None,
            reserved_topup=reserved_topup,
            reserved_daily=reserved_daily,
            actual_tokens=0,
            status="pending",
            reference=reference,
            description=description,
            expires_at=now_utc + timedelta(
                minutes=RESERVATION_TIMEOUT_MINUTES
            ),
        )

        db.add(reservation)

        if reserved_topup > 0:
            db.add(
                TokenTransaction(
                    id=str(uuid.uuid4()),
                    user_id=locked_user.id,
                    transaction_type="reserve",
                    amount=-reserved_topup,
                    balance_after=int(wallet.topup_balance),
                    reference=reference,
                    reservation_id=reservation.id,
                    description=(
                        f"Reservasi AI: {reserved_daily} token paket harian, "
                        f"{reserved_topup} token Top Up."
                    ),
                )
            )

        db.commit()
        db.refresh(reservation)

        # Atribut sementara ini tidak disimpan ke database.
        # Pemanggil meneruskannya ke batas output provider.
        reservation.max_output_tokens = allowed_output_tokens
        return reservation

    except Exception:
        db.rollback()
        raise


def cancel_reservation(
    db: Session,
    reservation_id: str,
    *,
    allow_needs_review: bool = False,
) -> None:
    """Batalkan reservasi dan kembalikan saldo Top Up tepat satu kali."""

    try:
        reservation_info = (
            db.query(TokenReservation.user_id)
            .filter(TokenReservation.id == reservation_id)
            .first()
        )

        if reservation_info is None:
            db.rollback()
            if allow_needs_review:
                raise HTTPException(
                    status_code=404,
                    detail="Reservasi tidak ditemukan.",
                )
            return

        user_id = reservation_info[0]
        if _lock_user(db, user_id) is None:
            db.rollback()
            if allow_needs_review:
                raise HTTPException(
                    status_code=404,
                    detail="Pengguna reservasi tidak ditemukan.",
                )
            return

        reservation = (
            db.query(TokenReservation)
            .filter(
                TokenReservation.id == reservation_id,
                TokenReservation.user_id == user_id,
            )
            .with_for_update()
            .first()
        )

        if reservation is None:
            db.rollback()
            if allow_needs_review:
                raise HTTPException(
                    status_code=404,
                    detail="Reservasi tidak ditemukan.",
                )
            return

        allowed_statuses = {"pending"}
        if allow_needs_review:
            allowed_statuses.add("needs_review")

        if reservation.status not in allowed_statuses:
            db.rollback()
            if allow_needs_review:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Hanya reservasi pending atau needs_review "
                        "yang dapat dibatalkan."
                    ),
                )
            return

        # Jangan refund jika bukti hasil AI sudah tersimpan,
        # termasuk bukti dengan data token yang belum lengkap.
        stored_result = (
            db.query(TokenReservationResult.reservation_id)
            .filter(
                TokenReservationResult.reservation_id == reservation_id
            )
            .first()
        )
        if stored_result is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bukti hasil AI sudah tersimpan. Reservasi tidak "
                    "boleh dibatalkan atau direfund."
                ),
            )

        wallet = _get_wallet(db, reservation.user_id)

        topup_refund = int(reservation.reserved_topup or 0)
        wallet.topup_balance += topup_refund

        reservation.status = "cancelled"
        reservation.completed_at = utc_now()

        if topup_refund > 0:
            db.add(
                TokenTransaction(
                    id=str(uuid.uuid4()),
                    user_id=reservation.user_id,
                    transaction_type="refund",
                    amount=topup_refund,
                    balance_after=int(wallet.topup_balance),
                    reference=reservation.reference,
                    reservation_id=reservation.id,
                    description=(
                        f"Refund reservasi: {topup_refund} token Top Up "
                        "dikembalikan."
                    ),
                )
            )

        db.commit()

    except Exception:
        db.rollback()
        raise


def complete_reservation(
    db: Session,
    reservation_id: str,
    actual_tokens: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    skill: str = "general",
    *,
    allow_needs_review: bool = False,
) -> None:
    """Settlement idempotent dengan pemisahan kuota paket dan Top Up."""

    actual_tokens = max(0, int(actual_tokens))
    prompt_tokens = max(0, int(prompt_tokens))
    completion_tokens = max(0, int(completion_tokens))

    try:
        reservation_info = (
            db.query(TokenReservation.user_id)
            .filter(TokenReservation.id == reservation_id)
            .first()
        )

        if reservation_info is None:
            raise HTTPException(
                status_code=409,
                detail="Reservasi tidak ditemukan.",
            )

        user_id = reservation_info[0]

        locked_user = _lock_user(db, user_id)
        if locked_user is None:
            raise HTTPException(
                status_code=404,
                detail="Pengguna reservasi tidak ditemukan.",
            )

        is_admin = locked_user.role == "admin"

        reservation = (
            db.query(TokenReservation)
            .filter(
                TokenReservation.id == reservation_id,
                TokenReservation.user_id == user_id,
            )
            .with_for_update()
            .first()
        )

        if reservation is None:
            raise HTTPException(
                status_code=409,
                detail="Reservasi tidak ditemukan.",
            )

        reserved_daily = int(reservation.reserved_daily or 0)
        reserved_topup = int(reservation.reserved_topup or 0)

        if is_admin:
            package_tokens = actual_tokens
            topup_actual = 0
        else:
            # Semua token aktual dihitung ke batas harian dan ditagihkan
            # ke saldo Top Up. Ini juga menangani reservasi lama yang
            # dahulu hanya mengisi reserved_daily tanpa memotong saldo.
            package_tokens = actual_tokens
            topup_actual = actual_tokens

        if reservation.status == "completed":
            usage = (
                db.query(TokenUsage)
                .filter(TokenUsage.reservation_id == reservation_id)
                .first()
            )
            transaction = (
                db.query(TokenTransaction)
                .filter(
                    TokenTransaction.reservation_id == reservation_id,
                    TokenTransaction.transaction_type == "usage",
                )
                .first()
            )

            settlement_matches = (
                usage is not None
                and transaction is not None
                and usage.user_id == user_id
                and transaction.user_id == user_id
                and transaction.transaction_type == "usage"
                and int(reservation.actual_tokens or 0) == actual_tokens
                and int(usage.total_tokens) == actual_tokens
                and int(usage.package_tokens or 0) == package_tokens
                and int(usage.prompt_tokens) == prompt_tokens
                and int(usage.completion_tokens) == completion_tokens
                and int(transaction.amount) == -topup_actual
            )

            if settlement_matches:
                db.rollback()
                return

            raise HTTPException(
                status_code=409,
                detail=(
                    "Reservasi sudah selesai, tetapi bukti settlement "
                    "tidak cocok. Tidak dilakukan pemotongan ulang."
                ),
            )

        allowed_statuses = {"pending"}
        if allow_needs_review:
            allowed_statuses.add("needs_review")

        if reservation.status not in allowed_statuses:
            raise HTTPException(
                status_code=409,
                detail="Status reservasi tidak diizinkan untuk settlement.",
            )

        wallet = _get_wallet(db, user_id)

        # Kembalikan bagian Top Up yang direservasi tetapi tidak digunakan.
        topup_refund = max(0, reserved_topup - topup_actual)
        extra = max(0, topup_actual - reserved_topup)

        # Settlement selalu membukukan seluruh pemakaian aktual.
        # Jika kelebihan reservasi melampaui saldo, selisih menjadi utang.
        # Top Up atau kredit Admin berikutnya akan melunasi utang tersebut.
        wallet.topup_balance += topup_refund - extra

        reservation.actual_tokens = actual_tokens
        reservation.status = "completed"
        reservation.completed_at = utc_now()

        db.add(
            TokenUsage(
                user_id=user_id,
                skill=skill,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=actual_tokens,
                package_tokens=package_tokens,
                reservation_id=reservation.id,
                created_at=reservation.created_at,
            )
        )

        db.add(
            TokenTransaction(
                id=str(uuid.uuid4()),
                user_id=user_id,
                transaction_type="usage",
                amount=-topup_actual,
                balance_after=int(wallet.topup_balance),
                reference=reservation.reference,
                description=(
                    f"Pemakaian aktual AI: {actual_tokens} token; "
                    f"paket harian: {package_tokens}; "
                    f"Top Up: {topup_actual}; "
                    f"refund Top Up: {topup_refund}; "
                    f"kelebihan reservasi: {extra}."
                ),
                reservation_id=reservation.id,
            )
        )

        db.commit()

    except Exception:
        db.rollback()
        raise
