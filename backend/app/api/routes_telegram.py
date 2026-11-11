import asyncio
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
)
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import (
    TELEGRAM_ENCRYPTION_KEY,
    TELEGRAM_WEBHOOK_BASE_URL,
)
from app.models.database import get_db
from app.models.conversation import Conversation, Message
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.telegram_account import TelegramAccount
from app.models.telegram_update import TelegramUpdate
from app.models.user import User
from app.api.routes_files import MAX_SIZE, _user_dir, extract_text
from app.services.master_agent import (
    master_agent,
    prepare_master_prompt,
    ProviderCallError,
)
from app.services.token_service import (
    reserve_tokens,
    cancel_reservation,
    complete_reservation,
)


router = APIRouter(prefix="/api/v1/telegram", tags=["telegram"])

# Ekstensi yang dapat diekstrak teksnya untuk dibaca AI.
TEXT_EXTRACT_EXT = {".pdf", ".docx", ".txt", ".md"}
# Ekstensi yang ditolak disimpan (mencegah penyalahgunaan).
BLOCKED_EXT = {".exe", ".bat", ".cmd", ".sh", ".scr", ".dll", ".com", ".msi"}
# Ekstensi default untuk media Telegram non-dokumen.
_MEDIA_EXT = {
    "photo": ".jpg",
    "video": ".mp4",
    "animation": ".gif",
    "audio": ".mp3",
    "voice": ".ogg",
}


def _extract_media(message: dict) -> dict | None:
    """Normalisasi pesan media Telegram (document/photo/video/audio/voice/
    animation) menjadi dict berisi file_id, file_name, file_size, ext."""
    if not isinstance(message, dict):
        return None

    doc = message.get("document")
    if isinstance(doc, dict) and doc.get("file_id"):
        name = Path(str(doc.get("file_name") or "dokumen")).name[:120] or "dokumen"
        return {
            "file_id": doc.get("file_id"),
            "file_name": name,
            "file_size": doc.get("file_size"),
            "ext": Path(name).suffix.lower(),
        }

    for key, ext in _MEDIA_EXT.items():
        media = message.get(key)
        if key == "photo":
            media = (
                max(media, key=lambda s: s.get("file_size") or 0)
                if isinstance(media, list) and media
                else None
            )
        if not isinstance(media, dict) or not media.get("file_id"):
            continue
        name = f"{key}_{str(media.get('file_id'))[:12]}{ext}"
        return {
            "file_id": media.get("file_id"),
            "file_name": name,
            "file_size": media.get("file_size"),
            "ext": ext,
        }

    return None


async def _download_telegram_document(
    token: str,
    document: dict,
    user_id: str,
) -> tuple[str, str, str, int]:
    """Unduh dan simpan dokumen Telegram ke direktori milik user."""
    telegram_file_id = document.get("file_id")
    declared_size = document.get("file_size")
    original_name = str(document.get("file_name") or "dokumen")[:120]
    ext = str(document.get("ext") or Path(original_name).suffix.lower())

    if not isinstance(telegram_file_id, str) or not telegram_file_id:
        raise ValueError("Identitas dokumen Telegram tidak valid.")

    if (
        isinstance(declared_size, bool)
        or not isinstance(declared_size, int)
        or declared_size < 1
        or declared_size > MAX_SIZE
    ):
        raise ValueError("Ukuran dokumen maksimal 5 MB.")

    if ext in BLOCKED_EXT:
        raise ValueError("Jenis file tidak diizinkan untuk disimpan.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        meta_response = await client.post(
            f"https://api.telegram.org/bot{token}/getFile",
            json={"file_id": telegram_file_id},
        )

        if meta_response.status_code != 200:
            raise ValueError("Telegram gagal menyiapkan dokumen.")

        meta = meta_response.json()
        file_path = (meta.get("result") or {}).get("file_path")

        if (
            not meta.get("ok")
            or not isinstance(file_path, str)
            or not file_path
            or ".." in Path(file_path).parts
            or Path(file_path).is_absolute()
        ):
            raise ValueError("Lokasi dokumen Telegram tidak valid.")

        file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
        chunks = []
        total_size = 0

        async with client.stream("GET", file_url) as response:
            if response.status_code != 200:
                raise ValueError("Gagal mengunduh dokumen dari Telegram.")

            async for chunk in response.aiter_bytes():
                total_size += len(chunk)
                if total_size > MAX_SIZE:
                    raise ValueError("Ukuran dokumen melebihi 5 MB.")
                chunks.append(chunk)

    if total_size == 0:
        raise ValueError("Dokumen yang dikirim kosong.")

    local_id = secrets.token_hex(6)
    user_dir = _user_dir(user_id)
    original_path = user_dir / f"{local_id}{ext}"
    extract_path = user_dir / f"{local_id}.extract.txt"

    try:
        original_path.write_bytes(b"".join(chunks))
        extracted = extract_text(original_path, ext) if ext in TEXT_EXTRACT_EXT else ""
        extract_path.write_text(extracted, encoding="utf-8")
        (user_dir / f"{local_id}.name.txt").write_text(
            original_name, encoding="utf-8"
        )
    except Exception:
        original_path.unlink(missing_ok=True)
        extract_path.unlink(missing_ok=True)
        (user_dir / f"{local_id}.name.txt").unlink(missing_ok=True)
        raise ValueError("File rusak atau tidak dapat dibaca.")

    return local_id, original_name, extracted, total_size


async def _send_telegram_document_checked(
    token: str,
    chat_id: int,
    path: Path,
    original_name: str,
) -> bool:
    """Kirim file asli melalui Telegram dan periksa hasilnya."""
    try:
        with path.open("rb") as file_handle:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": str(chat_id)},
                    files={
                        "document": (
                            Path(original_name).name[:120],
                            file_handle,
                            "application/octet-stream",
                        )
                    },
                )

        if response.status_code != 200:
            return False

        return bool(response.json().get("ok"))

    except (httpx.RequestError, OSError, ValueError):
        return False


class ConnectBotRequest(BaseModel):
    bot_token: SecretStr = Field(min_length=20, max_length=256)


def get_fernet() -> Fernet:
    if not TELEGRAM_ENCRYPTION_KEY:
        raise HTTPException(
            status_code=503,
            detail="Konfigurasi enkripsi Telegram belum tersedia.",
        )

    try:
        return Fernet(TELEGRAM_ENCRYPTION_KEY.encode())
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=503,
            detail="Konfigurasi enkripsi Telegram tidak valid.",
        )


def decrypt_bot_token(account: TelegramAccount) -> str:
    try:
        return get_fernet().decrypt(
            account.bot_token_encrypted.encode()
        ).decode()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Token bot tidak dapat dibaca. Periksa konfigurasi enkripsi.",
        )


def telegram_api(token: str, method: str, **kwargs):
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=kwargs,
            timeout=15.0,
        )
        data = response.json()
    except (httpx.RequestError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Tidak dapat menghubungi layanan Telegram.",
        )

    if response.status_code != 200 or not data.get("ok"):
        raise HTTPException(
            status_code=502,
            detail="Telegram gagal memproses permintaan.",
        )

    return data.get("result")


def send_telegram_message(token: str, chat_id: int, text: str):
    # Kegagalan pengiriman pesan tidak membatalkan proses linking
    # yang sudah tersimpan di database.
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
            timeout=12.0,
        )
    except httpx.RequestError:
        pass


@router.get("/status")
def telegram_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.user_id == current_user.id)
        .first()
    )

    if account is None:
        return {"connected": False, "status": "not_connected"}

    return {
        "connected": account.status == "active",
        "status": account.status,
        "bot_username": account.bot_username,
        "owner_linked": account.telegram_owner_id is not None,
    }


@router.delete("/disconnect")
def disconnect_telegram(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Kunci akun milik pengguna agar proses penghapusan tidak
    # berjalan bersamaan dengan aktivasi akun yang sama.
    account = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.user_id == current_user.id)
        .with_for_update()
        .first()
    )

    if account is None:
        raise HTTPException(
            status_code=404,
            detail="Koneksi Telegram tidak ditemukan.",
        )

    token = decrypt_bot_token(account)

    # Hapus webhook lebih dahulu. Jangan membuang update yang masih antre.
    # Jika Telegram gagal, data koneksi lokal tetap dipertahankan.
    telegram_api(
        token,
        "deleteWebhook",
        drop_pending_updates=False,
    )

    try:
        db.delete(account)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=(
                "Webhook Telegram sudah dilepas, tetapi data koneksi "
                "gagal dihapus. Silakan ulangi proses penghapusan."
            ),
        )

    return {
        "deleted": True,
        "message": (
            "Koneksi Telegram berhasil dihapus. "
            "Bot Token belum dicabut dari BotFather."
        ),
    }


@router.post("/connect", status_code=201)
def connect_telegram_bot(
    data: ConnectBotRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_active or current_user.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Akun harus aktif dan disetujui admin untuk menghubungkan bot.",
        )

    existing_account = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.user_id == current_user.id)
        .first()
    )
    if existing_account:
        raise HTTPException(
            status_code=409,
            detail="Akun ini sudah memiliki data bot Telegram. Putuskan koneksi lama terlebih dahulu.",
        )

    bot_token = data.bot_token.get_secret_value().strip()
    if not bot_token:
        raise HTTPException(status_code=422, detail="Bot Token tidak boleh kosong.")

    token_hash = hashlib.sha256(bot_token.encode()).hexdigest()

    duplicate_token = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.bot_token_hash == token_hash)
        .first()
    )
    if duplicate_token:
        raise HTTPException(
            status_code=409,
            detail="Bot Token tersebut sudah terhubung ke akun ONE MASTER AI lain.",
        )

    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{bot_token}/getMe",
            timeout=12.0,
        )
        telegram_data = response.json()
    except (httpx.TimeoutException, httpx.RequestError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Tidak dapat memvalidasi bot ke Telegram. Silakan coba lagi.",
        )

    if response.status_code != 200 or not telegram_data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail="Bot Token ditolak Telegram atau tidak valid.",
        )

    bot_info = telegram_data.get("result") or {}
    bot_id = bot_info.get("id")
    bot_username = bot_info.get("username")

    if (
        not isinstance(bot_id, int)
        or not isinstance(bot_username, str)
        or not bot_username
        or bot_info.get("is_bot") is not True
    ):
        raise HTTPException(
            status_code=502,
            detail="Telegram memberikan identitas bot yang tidak valid.",
        )

    duplicate_bot = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.bot_id == bot_id)
        .first()
    )
    if duplicate_bot:
        raise HTTPException(
            status_code=409,
            detail="Bot Telegram tersebut sudah terhubung ke akun lain.",
        )

    encrypted_token = get_fernet().encrypt(bot_token.encode()).decode()
    webhook_secret = secrets.token_urlsafe(32)

    account = TelegramAccount(
        user_id=current_user.id,
        bot_id=bot_id,
        bot_username=bot_username,
        bot_token_encrypted=encrypted_token,
        bot_token_hash=token_hash,
        webhook_secret=webhook_secret,
        status="pending",
    )

    db.add(account)
    try:
        db.commit()
        db.refresh(account)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Bot atau akun sudah terhubung. Muat ulang status koneksi.",
        )

    return {
        "connected": False,
        "status": account.status,
        "bot_username": account.bot_username,
        "message": (
            "Bot berhasil divalidasi dan token disimpan secara terenkripsi. "
            "Webhook dan pengaitan pemilik belum diaktifkan."
        ),
    }


@router.post("/activate")
def activate_telegram(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not current_user.is_active or current_user.status != "active":
        raise HTTPException(
            status_code=403,
            detail="Akun belum aktif dan disetujui administrator.",
        )

    account = (
        db.query(TelegramAccount)
        .filter(TelegramAccount.user_id == current_user.id)
        .with_for_update()
        .first()
    )

    if account is None:
        raise HTTPException(
            status_code=404,
            detail="Bot Telegram belum didaftarkan.",
        )

    if account.status != "approved":
        raise HTTPException(
            status_code=409,
            detail="Bot harus disetujui administrator sebelum aktivasi.",
        )

    if account.telegram_owner_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Pemilik Telegram sudah terhubung.",
        )

    token = decrypt_bot_token(account)
    webhook_url = (
        f"{TELEGRAM_WEBHOOK_BASE_URL}/api/v1/telegram/webhook/{account.id}"
    )

    # Jangan mengambil alih webhook milik layanan lain.
    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{token}/getWebhookInfo",
            timeout=15.0,
        )
        info_data = response.json()
    except (httpx.RequestError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Gagal memeriksa webhook Telegram.",
        )

    if response.status_code != 200 or not info_data.get("ok"):
        raise HTTPException(
            status_code=502,
            detail="Telegram gagal memberikan informasi webhook.",
        )

    existing_url = (info_data.get("result") or {}).get("url") or ""

    if existing_url and existing_url != webhook_url:
        raise HTTPException(
            status_code=409,
            detail=(
                "Bot sudah menggunakan webhook lain. "
                "Webhook lama tidak diubah demi keamanan."
            ),
        )

    # Simpan hash kode lebih dahulu agar webhook tidak menerima
    # perintah /start sebelum kode pengaitan tersedia di database.
    link_code = secrets.token_urlsafe(24)
    link_hash = hashlib.sha256(link_code.encode()).hexdigest()

    account.link_code_hash = link_hash
    account.link_code_expires_at = datetime.utcnow() + timedelta(minutes=15)
    account.last_error = None

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Kode pengaitan gagal disimpan. Silakan coba aktivasi kembali.",
        )

    # Setelah kode tersimpan, baru pasang webhook Telegram.
    try:
        telegram_api(
            token,
            "setWebhook",
            url=webhook_url,
            secret_token=account.webhook_secret,
            allowed_updates=["message"],
            drop_pending_updates=False,
        )
    except HTTPException:
        # Bersihkan kode jika pemasangan webhook gagal.
        account.link_code_hash = None
        account.link_code_expires_at = None
        account.last_error = "set_webhook_failed"
        db.commit()
        raise

    return {
        "status": "waiting_for_owner",
        "bot_username": account.bot_username,
        "expires_in_seconds": 900,
        "link_url": f"https://t.me/{account.bot_username}?start={link_code}",
        "message": (
            "Webhook berhasil dipasang. Buka tautan bot dan tekan Start "
            "untuk menghubungkan akun Telegram Anda."
        ),
    }



async def _send_telegram_message_checked(
    token: str,
    chat_id: int,
    text: str,
) -> bool:
    """Kirim pesan dan periksa respons aktual Telegram."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text[:4000],
                },
            )

        if response.status_code != 200:
            return False

        data = response.json()
        return bool(data.get("ok"))

    except (httpx.RequestError, ValueError):
        return False


async def _deliver_saved_telegram_reply(
    db: Session,
    account: TelegramAccount,
    update: TelegramUpdate,
    chat_id: int,
    token: str,
) -> bool:
    """
    Kirim jawaban yang sudah tersimpan.
    Tidak pernah memanggil AI kembali.
    """
    if not update.assistant_message_id:
        return False

    assistant_message = db.get(Message, update.assistant_message_id)
    if assistant_message is None:
        update.status = "needs_review"
        update.error_code = "assistant_message_missing"
        db.commit()
        return False

    # Verifikasi hasil dan reservasi sebelum jawaban dikirim.
    if update.reservation_id:
        stored_result = db.get(
            TokenReservationResult,
            update.reservation_id,
        )

        if stored_result is None:
            update.status = "needs_review"
            update.error_code = "reservation_result_missing"
            db.commit()
            return False

        if stored_result.status == "needs_review":
            update.status = "needs_review"
            update.error_code = "provider_result_needs_review"
            db.commit()
            return False

        if stored_result.status not in {"received", "settled"}:
            update.status = "needs_review"
            update.error_code = "invalid_reservation_result_status"
            db.commit()
            return False

        reservation = db.get(
            TokenReservation,
            update.reservation_id,
        )

        if (
            reservation is None
            or stored_result.user_id != reservation.user_id
            or stored_result.assistant_message_id != update.assistant_message_id
        ):
            update.status = "needs_review"
            update.error_code = "reservation_result_identity_mismatch"
            db.commit()
            return False

        # Status settled hanya sah jika reservasi sudah completed.
        # Jangan melakukan settlement baru untuk hasil yang mengaku settled.
        if (
            stored_result.status == "settled"
            and reservation.status != "completed"
        ):
            update.status = "needs_review"
            update.error_code = "settled_reservation_not_completed"
            db.commit()
            return False

        # Ketiga nilai wajib tersedia. Nilai nol tetap sah.
        token_values = (
            stored_result.prompt_tokens,
            stored_result.completion_tokens,
            stored_result.total_tokens,
        )

        if any(value is None for value in token_values):
            update.status = "needs_review"
            update.error_code = "reservation_token_usage_incomplete"
            db.commit()
            return False

        # Token dari provider harus berupa integer nonnegatif.
        # Total harus konsisten dengan prompt + completion.
        valid_token_values = all(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            for value in token_values
        )

        if not valid_token_values:
            update.status = "needs_review"
            update.error_code = "reservation_token_usage_invalid"
            db.commit()
            return False

        if stored_result.total_tokens != (
            stored_result.prompt_tokens + stored_result.completion_tokens
        ):
            update.status = "needs_review"
            update.error_code = "reservation_token_usage_mismatch"
            db.commit()
            return False

        try:
            # Fungsi ini idempotent: jika sudah completed, bukti settlement
            # diperiksa tanpa melakukan pemotongan saldo kedua kali.
            complete_reservation(
                db=db,
                reservation_id=update.reservation_id,
                actual_tokens=int(stored_result.total_tokens),
                prompt_tokens=int(stored_result.prompt_tokens),
                completion_tokens=int(stored_result.completion_tokens),
                skill=stored_result.skill,
            )

            db.refresh(reservation)

            if reservation.status != "completed":
                raise RuntimeError("Reservasi belum berstatus completed.")

            if stored_result.status == "received":
                stored_result.status = "settled"
                stored_result.settled_at = datetime.utcnow()
                db.commit()

        except Exception:
            db.rollback()
            update.status = "needs_review"
            update.error_code = "reservation_settlement_verification_failed"
            db.commit()
            return False

    # Periksa kembali status sebelum mengirim.
    db.refresh(update)
    if update.status == "completed":
        return True

    sent = await _send_telegram_message_checked(
        token,
        chat_id,
        assistant_message.content,
    )

    if not sent:
        update.status = "reply_pending"
        update.error_code = "telegram_send_failed"
        db.commit()
        return False

    update.status = "completed"
    update.error_code = None
    update.response_sent_at = datetime.utcnow()
    update.completed_at = datetime.utcnow()
    db.commit()
    return True


@router.post("/webhook/{account_id}")
async def telegram_webhook(
    account_id: str,
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
    db: Session = Depends(get_db),
):
    account = db.get(TelegramAccount, account_id)

    if account is None:
        raise HTTPException(status_code=404, detail="Webhook tidak ditemukan.")

    if (
        not x_telegram_bot_api_secret_token
        or not secrets.compare_digest(
            x_telegram_bot_api_secret_token,
            account.webhook_secret,
        )
    ):
        raise HTTPException(status_code=403, detail="Webhook secret tidak valid.")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Payload Telegram tidak valid.")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload Telegram harus berupa objek.")

    update_id = payload.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        raise HTTPException(status_code=400, detail="Update ID tidak valid.")

    message = payload.get("message")
    if not isinstance(message, dict):
        return {"ok": True, "ignored": True}

    sender = message.get("from")
    chat = message.get("chat")

    if not isinstance(sender, dict):
        sender = {}
    if not isinstance(chat, dict):
        chat = {}

    sender_id = sender.get("id")
    chat_id = chat.get("id")
    text = message.get("text")
    telegram_message_id = message.get("message_id")

    private_chat = (
        chat.get("type") == "private"
        and isinstance(sender_id, int)
        and not isinstance(sender_id, bool)
        and isinstance(chat_id, int)
        and not isinstance(chat_id, bool)
        and sender_id == chat_id
    )

    # Catat update terlebih dahulu. Unique constraint menjadi pagar
    # terhadap dua webhook dengan update_id yang sama.
    update = TelegramUpdate(
        id=str(uuid.uuid4()),
        account_id=account.id,
        update_id=update_id,
        telegram_message_id=(
            telegram_message_id
            if isinstance(telegram_message_id, int)
            and not isinstance(telegram_message_id, bool)
            else None
        ),
        status="processing",
    )

    db.add(update)

    try:
        db.commit()
        db.refresh(update)
    except IntegrityError:
        db.rollback()

        existing = (
            db.query(TelegramUpdate)
            .filter(
                TelegramUpdate.account_id == account.id,
                TelegramUpdate.update_id == update_id,
            )
            .first()
        )

        if existing is None:
            return {"ok": True, "duplicate": True}

        # Hanya reply_pending yang boleh dilanjutkan.
        # Status processing tidak diulang karena provider mungkin
        # sudah menerima permintaan sebelum proses sebelumnya terhenti.
        if (
            existing.status == "reply_pending"
            and private_chat
            and sender_id == account.telegram_owner_id
        ):
            token = decrypt_bot_token(account)
            delivered = await _deliver_saved_telegram_reply(
                db,
                account,
                existing,
                chat_id,
                token,
            )
            return {
                "ok": True,
                "duplicate": True,
                "reply_retried": delivered,
            }

        return {
            "ok": True,
            "duplicate": True,
            "status": existing.status,
        }

    now = datetime.utcnow()

    media = _extract_media(message)
    caption = message.get("caption")
    caption_text = caption.strip() if isinstance(caption, str) else ""

    if not private_chat or (not isinstance(text, str) and media is None):
        update.status = "completed"
        update.completed_at = now
        update.error_code = "ignored_non_private_or_unsupported_message"
        db.commit()
        return {"ok": True, "ignored": True}

    parts = text.strip().split(maxsplit=1) if isinstance(text, str) else caption_text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower() if parts else ""

    # Proses linking /start tetap dipisahkan dari chat AI.
    if command == "/start":
        code = parts[1].strip() if len(parts) == 2 else ""
        linked = False

        if code and len(code) <= 64:
            locked_account = (
                db.query(TelegramAccount)
                .filter(TelegramAccount.id == account_id)
                .with_for_update()
                .first()
            )

            supplied_hash = hashlib.sha256(code.encode()).hexdigest()

            valid_code = (
                locked_account is not None
                and locked_account.status == "approved"
                and locked_account.telegram_owner_id is None
                and locked_account.link_code_hash is not None
                and locked_account.link_code_expires_at is not None
                and locked_account.link_code_expires_at > now
                and secrets.compare_digest(
                    locked_account.link_code_hash,
                    supplied_hash,
                )
            )

            if valid_code:
                locked_account.telegram_owner_id = sender_id

                username = sender.get("username")
                locked_account.telegram_owner_username = (
                    username[:64] if isinstance(username, str) else None
                )

                locked_account.link_code_hash = None
                locked_account.link_code_expires_at = None
                locked_account.status = "active"
                locked_account.last_error = None
                linked = True

        update.status = "completed"
        update.completed_at = now
        db.commit()

        if linked:
            try:
                token = decrypt_bot_token(account)
                send_telegram_message(
                    token,
                    sender_id,
                    "Akun Telegram Anda berhasil terhubung dengan ONE MASTER AI.",
                )
            except HTTPException:
                pass

        return {"ok": True, "linked": linked}

    # Semua pesan AI hanya boleh berasal dari pemilik bot.
    if (
        account.status != "active"
        or account.telegram_owner_id is None
        or sender_id != account.telegram_owner_id
    ):
        update.status = "completed"
        update.completed_at = now
        update.error_code = "unauthorized_sender_or_inactive_bot"
        db.commit()
        return {"ok": True, "ignored": True}

    user = db.get(User, account.user_id)

    if user is None or not user.is_active or user.status != "active":
        update.status = "failed"
        update.error_code = "user_inactive"
        update.completed_at = now
        db.commit()

        token = decrypt_bot_token(account)
        await _send_telegram_message_checked(
            token,
            chat_id,
            "Akun ONE MASTER AI belum aktif. Silakan hubungi admin.",
        )
        return {"ok": True, "error": "user_inactive"}

    # Perintah pengelolaan file tidak menggunakan AI atau token.
    if command == "/files":
        user_dir = _user_dir(user.id)
        entries = []

        for path in sorted(user_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            if not path.is_file():
                continue
            if not path.stem.isalnum():
                continue

            name_path = user_dir / f"{path.stem}.name.txt"
            original_name = (
                name_path.read_text(encoding="utf-8", errors="ignore").strip()
                if name_path.is_file()
                else path.name
            )
            entries.append(
                f"ID: {path.stem}\nNama: {original_name[:120]}\nUkuran: {path.stat().st_size} byte"
            )

        if entries:
            reply = "FILE ANDA (maksimal 20 terbaru):\n\n" + "\n\n".join(entries[:20])
            reply += "\n\nUnduh: /download ID_FILE · Hapus: /delete ID_FILE · Perbarui: kirim file + caption /update ID_FILE"
        else:
            reply = "Belum ada file tersimpan. Kirim file apa pun (PDF, DOCX, TXT, gambar, video, audio, ZIP, dll.) maksimal 5 MB ke bot."

        token = decrypt_bot_token(account)
        sent = await _send_telegram_message_checked(token, chat_id, reply)
        update.status = "completed" if sent else "failed"
        update.error_code = None if sent else "telegram_send_failed"
        update.completed_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "command": "files", "sent": sent}

    if command == "/download":
        file_id = parts[1].strip().lower() if len(parts) == 2 else ""

        if len(file_id) != 12 or any(c not in "0123456789abcdef" for c in file_id.lower()):
            reply = "Format perintah: /download ID_FILE\nGunakan /files untuk melihat ID."
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(token, chat_id, reply)
            update.status = "completed" if sent else "failed"
            update.error_code = None if sent else "telegram_send_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "command": "download", "sent": sent}

        user_dir = _user_dir(user.id)
        matches = [
            p
            for p in user_dir.glob(f"{file_id}.*")
            if p.is_file()
            and not p.name.endswith(".name.txt")
            and not p.name.endswith(".extract.txt")
        ]

        if len(matches) != 1:
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(
                token, chat_id, "File tidak ditemukan di akun Anda."
            )
            update.status = "completed" if sent else "failed"
            update.error_code = None if sent else "telegram_send_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "command": "download", "sent": sent}

        path = matches[0]
        if path.stat().st_size > MAX_SIZE:
            reply = "File melebihi batas pengiriman 5 MB."
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(token, chat_id, reply)
        else:
            name_path = user_dir / f"{file_id}.name.txt"
            original_name = (
                name_path.read_text(encoding="utf-8", errors="ignore").strip()
                if name_path.is_file()
                else path.name
            )
            token = decrypt_bot_token(account)
            sent = await _send_telegram_document_checked(
                token, chat_id, path, original_name
            )

        update.status = "completed" if sent else "failed"
        update.error_code = None if sent else "telegram_send_failed"
        update.completed_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "command": "download", "sent": sent}

    if command == "/delete":
        file_id = parts[1].strip().lower() if len(parts) == 2 else ""

        if len(file_id) != 12 or any(c not in "0123456789abcdef" for c in file_id.lower()):
            reply = "Format perintah: /delete ID_FILE\nGunakan /files untuk melihat ID."
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(token, chat_id, reply)
            update.status = "completed" if sent else "failed"
            update.error_code = None if sent else "telegram_send_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "command": "delete", "sent": sent}

        user_dir = _user_dir(user.id)
        targets = [p for p in user_dir.glob(f"{file_id}.*") if p.is_file()]
        for p in targets:
            p.unlink(missing_ok=True)

        reply = "File berhasil dihapus." if targets else "File tidak ditemukan di akun Anda."
        token = decrypt_bot_token(account)
        sent = await _send_telegram_message_checked(token, chat_id, reply)
        update.status = "completed" if sent else "failed"
        update.error_code = None if sent else "telegram_send_failed"
        update.completed_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "command": "delete", "sent": sent}

    if command == "/update":
        if media is None:
            reply = "Kirim file baru dengan caption /update ID_FILE.\nGunakan /files untuk melihat ID."
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(token, chat_id, reply)
            update.status = "completed" if sent else "failed"
            update.error_code = None if sent else "telegram_send_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "command": "update", "sent": sent}

        file_id = parts[1].strip().lower() if len(parts) == 2 else ""
        if len(file_id) != 12 or any(c not in "0123456789abcdef" for c in file_id.lower()):
            reply = "Format perintah: /update ID_FILE\nKirim file baru dengan caption /update ID_FILE."
            token = decrypt_bot_token(account)
            sent = await _send_telegram_message_checked(token, chat_id, reply)
            update.status = "completed" if sent else "failed"
            update.error_code = None if sent else "telegram_send_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            return {"ok": True, "command": "update", "sent": sent}

        token = decrypt_bot_token(account)
        try:
            new_id, original_name, _, file_size = await _download_telegram_document(
                token=token,
                document=media,
                user_id=user.id,
            )
        except (ValueError, httpx.RequestError):
            update.status = "failed"
            update.error_code = "telegram_document_download_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            await _send_telegram_message_checked(
                token,
                chat_id,
                "File gagal diproses. Pastikan ukurannya maksimal 5 MB.",
            )
            return {"ok": True, "status": "update_failed"}

        user_dir = _user_dir(user.id)
        for p in list(user_dir.glob(f"{file_id}.*")):
            p.unlink(missing_ok=True)

        renamed = 0
        for p in list(user_dir.glob(f"{new_id}.*")):
            suffix = p.name[len(new_id):]
            p.rename(user_dir / f"{file_id}{suffix}")
            renamed += 1

        reply = (
            f"File {file_id} berhasil diperbarui ({original_name}, {file_size} byte)."
            if renamed
            else "Gagal memperbarui file."
        )
        sent = await _send_telegram_message_checked(token, chat_id, reply)
        update.status = "completed" if sent else "failed"
        update.error_code = None if sent else "telegram_send_failed"
        update.completed_at = datetime.utcnow()
        db.commit()
        return {"ok": True, "command": "update", "sent": sent}

    file_text = ""

    if media is not None:
        token = decrypt_bot_token(account)

        try:
            file_id, original_name, extracted_text, file_size = (
                await _download_telegram_document(
                    token=token,
                    document=media,
                    user_id=user.id,
                )
            )
        except (ValueError, httpx.RequestError):
            update.status = "failed"
            update.error_code = "telegram_document_download_failed"
            update.completed_at = datetime.utcnow()
            db.commit()
            await _send_telegram_message_checked(
                token,
                chat_id,
                "File gagal diproses. Pastikan ukurannya maksimal 5 MB.",
            )
            return {"ok": True, "status": "document_failed"}

        caption = message.get("caption")
        user_message = (
            caption.strip()
            if isinstance(caption, str) and caption.strip()
            else "Mohon analisis dokumen yang saya kirimkan."
        )
        user_message += (
            f"\n\n[Dokumen terlampir: {original_name}; "
            f"ID file: {file_id}; ukuran: {file_size} byte]"
        )
        file_text = extracted_text[:6000]

    else:
        user_message = text.strip()

    if not user_message:
        update.status = "completed"
        update.completed_at = now
        db.commit()
        return {"ok": True, "ignored": True}

    # Buat atau gunakan percakapan khusus bot ini.
    conv = None
    if account.conversation_id:
        conv = db.get(Conversation, account.conversation_id)
        if conv is not None and conv.user_id != user.id:
            conv = None

    if conv is None:
        conv = Conversation(
            user_id=user.id,
            title=f"Telegram - @{account.bot_username}",
        )
        db.add(conv)
        db.flush()
        account.conversation_id = conv.id
        db.commit()
        db.refresh(conv)

    history = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(10)
        .all()
    )
    history = list(reversed(history))

    history_text = "\n".join(
        f"{'User' if item.role == 'user' else 'Asisten'}: {item.content[:500]}"
        for item in history
    )

    prepared = prepare_master_prompt(
        user_message=user_message,
        profession=user.profession or "umum",
        full_name=user.full_name or "",
        history_text=history_text,
        file_text=file_text,
        user_role=user.role or "user",
    )

    estimated_tokens = max(
        1,
        (len(prepared["prompt"]) + 2) // 3,
    ) + 512

    reservation = None

    try:
        reservation = reserve_tokens(
            db=db,
            user=user,
            estimated_tokens=estimated_tokens,
            reference=f"telegram:{account.id}:{update_id}",
            description="Chat Telegram ONE MASTER AI",
        )
    except HTTPException as exc:
        update.status = "failed"
        update.error_code = f"reservation_rejected_{exc.status_code}"
        update.completed_at = datetime.utcnow()
        db.commit()

        token = decrypt_bot_token(account)
        await _send_telegram_message_checked(
            token,
            chat_id,
            str(exc.detail)[:3500],
        )
        return {"ok": True, "error": "reservation_rejected"}

    db.add(
        Message(
            conversation_id=conv.id,
            role="user",
            content=user_message,
        )
    )
    db.commit()

    try:
        result = await asyncio.to_thread(
            master_agent,
            user_message=user_message,
            profession=user.profession or "umum",
            history_text=history_text,
            file_text=file_text,
            prepared=prepared,
            model_id="default",
        )

    except ProviderCallError as exc:
        if reservation is not None and exc.safe_to_refund:
            try:
                cancel_reservation(db, reservation.id)
            except Exception:
                update.status = "needs_review"
                update.error_code = "safe_refund_failed"
                db.commit()
                return {"ok": True, "status": "needs_review"}

            update.status = "failed"
            update.error_code = "provider_safe_failure"
            update.completed_at = datetime.utcnow()
            db.commit()

            token = decrypt_bot_token(account)
            await _send_telegram_message_checked(
                token,
                chat_id,
                "Provider AI sedang gagal memproses permintaan. "
                "Reservasi token telah dikembalikan. Silakan coba lagi.",
            )
            return {"ok": True, "status": "failed_refunded"}

        # Hasil tidak pasti: saldo ditahan dan tidak ada pemanggilan ulang.
        if reservation is not None:
            locked_reservation = db.get(TokenReservation, reservation.id)
            if locked_reservation is not None and locked_reservation.status == "pending":
                locked_reservation.status = "needs_review"
                locked_reservation.description = (
                    (locked_reservation.description or "")
                    + f" | Telegram provider review: {exc.reason}"
                )[:2000]

            if exc.answer:
                assistant_message = Message(
                    conversation_id=conv.id,
                    role="assistant",
                    content=exc.answer,
                    skill=prepared["skill"],
                )
                db.add(assistant_message)
                db.flush()

                db.add(
                    TokenReservationResult(
                        reservation_id=reservation.id,
                        user_id=user.id,
                        assistant_message_id=assistant_message.id,
                        response_content=exc.answer,
                        skill=prepared["skill"],
                        prompt_tokens=exc.prompt_tokens,
                        completion_tokens=exc.completion_tokens,
                        total_tokens=None,
                        status="needs_review",
                    )
                )

        update.status = "needs_review"
        update.error_code = "provider_result_uncertain"
        db.commit()
        return {"ok": True, "status": "needs_review"}

    except Exception:
        if reservation is not None:
            locked_reservation = db.get(TokenReservation, reservation.id)
            if locked_reservation is not None and locked_reservation.status == "pending":
                locked_reservation.status = "needs_review"
                locked_reservation.description = (
                    (locked_reservation.description or "")
                    + " | Telegram unexpected provider error"
                )[:2000]

            update.status = "needs_review"
            update.error_code = "unexpected_provider_error"
        else:
            update.status = "failed"
            update.error_code = "unexpected_admin_provider_error"

        db.commit()
        return {"ok": True, "status": update.status}

    # Simpan jawaban sebelum settlement dan sebelum mengirim ke Telegram.
    assistant_message = Message(
        conversation_id=conv.id,
        role="assistant",
        content=result["answer"],
        skill=result["skill"],
    )
    db.add(assistant_message)
    db.flush()

    update.assistant_message_id = assistant_message.id
    update.reservation_id = reservation.id if reservation else None
    update.status = "reply_pending"
    update.error_code = None

    if reservation is not None:
        db.add(
            TokenReservationResult(
                reservation_id=reservation.id,
                user_id=user.id,
                assistant_message_id=assistant_message.id,
                response_content=result["answer"],
                skill=result["skill"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"],
                total_tokens=result["total_tokens"],
                status="received",
            )
        )
    else:
        db.add(
            TokenUsage(
                user_id=user.id,
                skill=result["skill"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"],
                total_tokens=result["total_tokens"],
            )
        )

    db.commit()

    token = decrypt_bot_token(account)
    delivered = await _deliver_saved_telegram_reply(
        db,
        account,
        update,
        chat_id,
        token,
    )

    return {
        "ok": True,
        "status": "completed" if delivered else "reply_pending",
    }

