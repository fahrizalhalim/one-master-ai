"""Kebijakan penyimpanan personal ONE MASTER AI.

Modul ini hanya menyediakan pemeriksaan kebijakan dan perhitungan pemakaian.
Tidak melakukan penghapusan file maupun mengubah database.
"""

import stat
import fcntl
import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.subscription import (
    SubscriptionPlan,
    TokenTransaction,
    UserSubscription,
)
from app.models.topup import TopUpRequest


# 13 November 2026 pukul 00.00 Asia/Shanghai
# Sama dengan 12 November 2026 pukul 16.00 UTC.
POLICY_START_UTC = datetime(2026, 11, 12, 16, 0, 0)

WIT = ZoneInfo("Asia/Jayapura")

MB = 1_000_000

STORAGE_QUOTAS = {
    "starter": 75 * MB,
    "regular": 100 * MB,
    "professional": 150 * MB,
    "business": 200 * MB,
}

PACKAGE_ALIASES = {
    "basic": "starter",
    "pro": "regular",
    "premium": "professional",
}


def _now_wit() -> datetime:
    """Waktu WIT tanpa tzinfo, mengikuti format tanggal langganan DB."""
    return datetime.now(WIT).replace(tzinfo=None)


def _canonical_package(code: str | None) -> str | None:
    if not code:
        return None

    normalized = code.strip().lower()
    return PACKAGE_ALIASES.get(normalized, normalized)


def _has_successful_topup(db: Session, user_id: str) -> bool:
    """Top Up valid harus approved dan memiliki transaksi kredit terkait."""
    approved_ids = [
        row[0]
        for row in (
            db.query(TopUpRequest.id)
            .filter(
                TopUpRequest.user_id == user_id,
                TopUpRequest.status == "approved",
            )
            .all()
        )
    ]

    if not approved_ids:
        return False

    references = [f"topup:{request_id}" for request_id in approved_ids]

    return (
        db.query(TokenTransaction.id)
        .filter(
            TokenTransaction.user_id == user_id,
            TokenTransaction.reference.in_(references),
        )
        .first()
        is not None
    )


def _latest_subscription(
    db: Session,
    user_id: str,
) -> tuple[UserSubscription, SubscriptionPlan] | None:
    return (
        db.query(UserSubscription, SubscriptionPlan)
        .join(
            SubscriptionPlan,
            SubscriptionPlan.id == UserSubscription.plan_id,
        )
        .filter(UserSubscription.user_id == user_id)
        .order_by(
            UserSubscription.current_period_start.desc(),
            UserSubscription.created_at.desc(),
            UserSubscription.id.desc(),
        )
        .first()
    )


def _latest_approved_package(db: Session, user_id: str) -> str | None:
    """Fallback untuk data Top Up yang tidak memiliki riwayat langganan."""
    item = (
        db.query(TopUpRequest)
        .filter(
            TopUpRequest.user_id == user_id,
            TopUpRequest.status == "approved",
        )
        .order_by(TopUpRequest.reviewed_at.desc(), TopUpRequest.created_at.desc())
        .first()
    )

    return _canonical_package(item.package_code) if item else None


def get_storage_policy(
    db: Session,
    user,
    now: datetime | None = None,
) -> dict:
    """Menghasilkan status kebijakan penyimpanan seorang pengguna.

    Status:
      exempt                : admin atau akun uji coba sebelum kebijakan
      not_eligible          : akun baru belum pernah Top Up berhasil
      active                : masa paket masih aktif
      grace                 : tujuh hari setelah paket berakhir
      upload_locked         : hari ke-8 sampai sebelum hari ke-30
      purge_due             : sudah mencapai hari ke-30
      subscription_unavailable: riwayat paket tidak tersedia atau tidak valid
    """
    now = now or _now_wit()

    created_at = user.created_at

    # Admin selalu dikecualikan dari kebijakan storage paket.
    if getattr(user, "role", None) == "admin":
        return {
            "status": "exempt",
            "exemption_reason": "admin",
            "eligible": False,
            "package_code": None,
            "quota_bytes": None,
            "quota_mb": None,
            "upload_allowed": True,
            "purge_due": False,
            "expires_at": None,
        }

    if created_at is None:
        return {
            "status": "not_eligible",
            "eligible": False,
            "package_code": None,
            "quota_bytes": None,
            "quota_mb": None,
            "upload_allowed": True,
            "purge_due": False,
            "expires_at": None,
        }

    # Semua akun yang sudah terdaftar sebelum kebijakan dimulai
    # merupakan akun uji coba saat ini dan dikecualikan.
    # created_at users_jwt disimpan sebagai UTC-naive.
    if created_at < POLICY_START_UTC:
        return {
            "status": "exempt",
            "exemption_reason": "pre_policy_test_account",
            "eligible": False,
            "package_code": None,
            "quota_bytes": None,
            "quota_mb": None,
            "upload_allowed": True,
            "purge_due": False,
            "expires_at": None,
        }

    if not _has_successful_topup(db, user.id):
        return {
            "status": "not_eligible",
            "eligible": False,
            "package_code": None,
            "quota_bytes": None,
            "quota_mb": None,
            "upload_allowed": True,
            "purge_due": False,
            "expires_at": None,
        }

    latest = _latest_subscription(db, user.id)

    if latest is None:
        package_code = _latest_approved_package(db, user.id)
        quota = STORAGE_QUOTAS.get(package_code)

        return {
            "status": "subscription_unavailable",
            "eligible": True,
            "package_code": package_code,
            "quota_bytes": quota,
            "quota_mb": quota // MB if quota is not None else None,
            "upload_allowed": False,
            "purge_due": False,
            "expires_at": None,
        }

    subscription, plan = latest
    package_code = _canonical_package(plan.code)
    quota = STORAGE_QUOTAS.get(package_code)

    if quota is None:
        return {
            "status": "subscription_unavailable",
            "eligible": True,
            "package_code": package_code,
            "quota_bytes": None,
            "quota_mb": None,
            "upload_allowed": False,
            "purge_due": False,
            "expires_at": subscription.current_period_end.isoformat(),
        }

    expires_at = subscription.current_period_end
    grace_ends_at = expires_at + timedelta(days=7)
    purge_at = expires_at + timedelta(days=30)

    if subscription.status == "active" and now < expires_at:
        status = "active"
        upload_allowed = True
        purge_due = False
    elif (
        subscription.status in {"active", "expired"}
        and expires_at <= now <= grace_ends_at
    ):
        status = "grace"
        upload_allowed = True
        purge_due = False
    elif (
        subscription.status in {"active", "expired"}
        and grace_ends_at < now < purge_at
    ):
        status = "upload_locked"
        upload_allowed = False
        purge_due = False
    elif (
        subscription.status in {"active", "expired"}
        and now >= purge_at
    ):
        status = "purge_due"
        upload_allowed = False
        purge_due = True
    else:
        status = "subscription_unavailable"
        upload_allowed = False
        purge_due = False

    return {
        "status": status,
        "eligible": True,
        "package_code": package_code,
        "quota_bytes": quota,
        "quota_mb": quota // MB,
        "upload_allowed": upload_allowed,
        "purge_due": purge_due,
        "expires_at": expires_at.isoformat(),
        "grace_ends_at": grace_ends_at.isoformat(),
        "purge_at": purge_at.isoformat(),
    }


def calculate_storage_usage(
    user_dir: Path,
    exclude_file_id: str | None = None,
) -> int:
    """Menghitung file asli; sidecar, symlink, dan ID yang dikecualikan dilewati."""
    user_dir = Path(user_dir)

    if not user_dir.exists() or not user_dir.is_dir():
        return 0

    total = 0

    for path in user_dir.rglob("*"):
        try:
            if path.is_symlink() or path.name.startswith("."):
                continue

            if path.name.endswith((".extract.txt", ".name.txt")):
                continue

            if exclude_file_id and path.name.startswith(f"{exclude_file_id}."):
                continue

            metadata = path.lstat()
            if stat.S_ISREG(metadata.st_mode):
                total += metadata.st_size

        except (FileNotFoundError, PermissionError, OSError):
            # File yang sedang berubah atau tidak dapat dibaca tidak
            # boleh menyebabkan pemeriksaan kuota menggagalkan layanan.
            continue

    return total



@contextmanager
def user_storage_lock(user_id: str):
    """Kunci lintas proses untuk operasi storage milik satu pengguna."""
    digest = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()
    lock_path = Path("/tmp") / f"oma-storage-{digest}.lock"

    with lock_path.open("a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
