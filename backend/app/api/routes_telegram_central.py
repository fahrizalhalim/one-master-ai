"""Telegram bot pusat ONE MASTER AI.

Router ini berdampingan dengan sistem Telegram per-user yang lama.
Tidak mengubah webhook, token, atau data bot lain.
"""

import hashlib
import secrets
import re
from datetime import datetime, timedelta
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.routes_chat import ChatRequest, _chat_impl
from app.core.config import (
    OMAI_TELEGRAM_BOT_TOKEN,
    OMAI_TELEGRAM_BOT_USERNAME,
    OMAI_TELEGRAM_WEBHOOK_SECRET,
)
from app.models.conversation import Conversation, Message
from app.models.database import engine, get_db
from app.models.telegram_central_link import TelegramCentralLink
from app.models.telegram_central_update import TelegramCentralUpdate
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.user import User
from app.services.telegram_workspace_service import (
    WorkspaceError,
    workspace_menu_text,
    list_user_files,
    search_user_files,
    list_user_folders,
    create_user_folder,
    rename_user_folder,
    delete_user_folder,
    storage_user_info,
    preview_user_file,
    get_user_file_extension,
    send_user_image_preview,
    rename_user_file,
    delete_user_file,
    upload_telegram_document,
    send_user_file,
    create_user_document,
    send_generated_document,
)


router = APIRouter(
    prefix="/api/v1/telegram-central",
    tags=["telegram-central"],
)

LINK_CODE_MINUTES = 15


def _utc_now() -> datetime:
    return datetime.utcnow()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _detect_document_request(message: str) -> tuple[str, str] | None:
    """Deteksi permintaan pembuatan dokumen dan format output."""
    value = str(message or "").strip()
    if not value:
        return None

    intent = re.search(
        r"\b(buat(?:kan)?|bikin|susun|siapkan|hasilkan|jadikan(?:kan)?|"
        r"jadi(?:kan)?|ekspor|export|convert|konversi|ubah(?:kan)?|"
        r"simpan(?:kan)?)\b",
        value,
        flags=re.IGNORECASE,
    )
    document = re.search(
        r"\b(dokumen|laporan|surat|proposal|makalah|"
        r"artikel|notulen|resume|cv|daftar|tabel|"
        r"presentasi|slide|spreadsheet|brosur|file|berkas)\b",
        value,
        flags=re.IGNORECASE,
    )
    # Format keluaran eksplisit (pdf/word/excel/ppt) + konteks "chat di atas"
    # juga sah sebagai permintaan dokumen, meski tanpa kata benda dokumen.
    # (kasus nyata: "Buat isi chat di atas menjadi pdf" sempat tidak terdeteksi
    # sehingga AI menjawab tidak bisa membuat file.)
    format_only = re.search(
        r"\b(chat di atas|isi chat|jawaban (?:di )?atas|hasil (?:di )?atas|"
        r"percakapan (?:di )?atas|balasan (?:di )?atas)\b",
        value,
        flags=re.IGNORECASE,
    )

    if not intent or not (document or format_only):
        return None

    # Pertanyaan CARA (bukan permintaan): "cara membuat pdf", "bagaimana
    # convert ke excel" — pengguna ingin panduan, bukan file.
    if re.match(r"^\s*(cara|bagaimana|tutorial|langkah)", value, flags=re.IGNORECASE):
        return None

    formats = [
        (r"\bpdf\b", "pdf"),
        (r"\b(docx|word)\b", "docx"),
        (r"\bdoc\b", "doc"),
        (r"\b(xlsx|excel)\b", "xlsx"),
        (r"\bxls\b", "xls"),
        (r"\b(pptx|powerpoint|power\s*point)\b", "pptx"),
        (r"\bppt\b", "ppt"),
        (r"\bmarkdown\b|\bmd\b", "md"),
        (r"\btxt\b|\btext\b", "txt"),
        (r"\bjpeg\b", "jpeg"),
        (r"\bjpg\b", "jpg"),
        (r"\bpng\b", "png"),
    ]

    selected_format = "docx"
    for pattern, file_format in formats:
        if re.search(pattern, value, flags=re.IGNORECASE):
            selected_format = file_format
            break

    explicit_title = re.search(
        r"\bjudul\s*:\s*(.+)",
        value,
        flags=re.IGNORECASE,
    )

    if explicit_title:
        title = explicit_title.group(1).strip().splitlines()[0][:100]
    elif document:
        title_match = document.group(1)
        title = title_match.capitalize()

        if title.lower() in {"dokumen", "file", "berkas"}:
            title = "Dokumen_AI"
    else:
        # Format-only tanpa kata benda dokumen (mis. "isi chat di atas").
        title = "Jawaban_Chat"

    return selected_format, title


def _workspace_menu_markup() -> dict:
    return {
        "keyboard": [
            [{"text": "💬 Chat AI"}, {"text": "📚 Riwayat Chat"}],
            [{"text": "📁 File Saya"}, {"text": "📄 Buat Dokumen"}],
            [{"text": "🪙 Token & Top Up"}, {"text": "👤 Profil & Bantuan"}],
            [{"text": "🏠 Menu Utama"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


async def _send_workspace_menu(
    chat_id: int,
    greeting: str | None = None,
) -> bool | None:
    heading = greeting or "Selamat datang kembali di AI Workspace OM.AI."
    menu_text = (
        f"{heading}\n\n"
        "Pilih layanan melalui tombol di bawah ini.\n\n"
        "💬 Chat AI — percakapan dengan asisten AI\n"
        "📚 Riwayat Chat — percakapan sebelumnya\n"
        "📁 File Saya — layanan berkas personal\n"
        "📄 Buat Dokumen — pembuatan dokumen\n"
        "🪙 Token & Top Up — kuota dan pengisian saldo\n"
        "👤 Profil & Bantuan — informasi akun dan panduan"
    )
    return await _send_message(
        chat_id,
        menu_text,
        reply_markup=_workspace_menu_markup(),
    )


async def _send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> bool | None:
    """Kirim pesan melalui bot pusat.

    True  = seluruh bagian terkonfirmasi terkirim.
    False = kegagalan ditolak secara eksplisit sebelum ada bagian terkirim.
    None  = hasil tidak pasti; jangan mengirim ulang otomatis.
    """
    if not OMAI_TELEGRAM_BOT_TOKEN:
        return False

    chunks = [
        text[index:index + 4000]
        for index in range(0, len(text), 4000)
    ] or [""]

    sent_any = False

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for chunk in chunks:
                payload_data = {"chat_id": chat_id, "text": chunk}
                if reply_markup is not None and len(chunks) == 1:
                    payload_data["reply_markup"] = reply_markup

                response = await client.post(
                    f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendMessage",
                    json=payload_data,
                )

                if response.status_code != 200:
                    return None if sent_any else False

                payload = response.json()
                if not isinstance(payload, dict) or not payload.get("ok"):
                    return None if sent_any else False

                sent_any = True

        return True

    except (httpx.RequestError, ValueError):
        return None


def _record_update(
    db: Session,
    update_id: int,
    telegram_user_id: int | None,
    telegram_message_id: int | None,
) -> tuple[TelegramCentralUpdate | None, bool]:
    """Simpan update sebelum diproses; update_id global harus unik."""
    update = TelegramCentralUpdate(
        update_id=update_id,
        telegram_user_id=telegram_user_id,
        telegram_message_id=telegram_message_id,
        status="processing",
    )
    db.add(update)

    try:
        db.commit()
        db.refresh(update)
        return update, False
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(TelegramCentralUpdate)
            .filter(TelegramCentralUpdate.update_id == update_id)
            .first()
        )
        return existing, True



def _delivery_lock_key(update_id: int) -> str:
    return f"omai:telegram-central:delivery:{update_id}"


def _acquire_delivery_lock(update_id: int):
    """Ambil session-level advisory lock pada koneksi PostgreSQL khusus."""
    connection = engine.connect()
    try:
        acquired = connection.execute(
            text(
                "SELECT pg_try_advisory_lock("
                "hashtextextended(:lock_key, 0))"
            ),
            {"lock_key": _delivery_lock_key(update_id)},
        ).scalar_one()

        if not acquired:
            connection.close()
            return None

        return connection
    except Exception:
        connection.invalidate()
        connection.close()
        raise


def _release_delivery_lock(connection) -> None:
    """Lepaskan advisory lock; putuskan koneksi jika unlock gagal."""
    try:
        unlocked = connection.execute(
            text(
                "SELECT pg_advisory_unlock("
                "hashtextextended(:lock_key, 0))"
            ),
            {"lock_key": _delivery_lock_key_from_connection(connection)},
        ).scalar_one()

        connection.commit()

        if not unlocked:
            connection.invalidate()
            raise RuntimeError("Advisory lock tidak berhasil dilepaskan.")
    except Exception:
        connection.invalidate()
        raise
    finally:
        connection.close()


def _delivery_lock_key_from_connection(connection) -> str:
    return connection.info["telegram_central_delivery_lock_key"]


async def _deliver_saved_reply(
    db: Session,
    update: TelegramCentralUpdate,
    chat_id: int,
) -> bool:
    """Kirim jawaban dengan advisory lock lintas proses."""
    connection = _acquire_delivery_lock(update.update_id)

    if connection is None:
        return False

    connection.info["telegram_central_delivery_lock_key"] = (
        _delivery_lock_key(update.update_id)
    )

    try:
        return await _deliver_saved_reply_locked(db, update, chat_id)
    finally:
        _release_delivery_lock(connection)


async def _deliver_saved_reply_locked(
    db: Session,
    update: TelegramCentralUpdate,
    chat_id: int,
) -> bool:
    """Validasi seluruh bukti jawaban dan settlement sebelum pengiriman."""
    locked_update = (
        db.query(TelegramCentralUpdate)
        .filter(TelegramCentralUpdate.id == update.id)
        .populate_existing()
        .with_for_update()
        .first()
    )

    if locked_update is None:
        return False

    update = locked_update

    if update.status == "completed":
        return True

    if update.status not in {"processing", "reply_pending"}:
        return False

    def reject(error_code: str) -> bool:
        update.status = "needs_review"
        update.error_code = error_code
        db.commit()
        return False

    if not update.user_id or not update.reservation_id:
        return reject("reservation_or_user_missing")

    if not update.assistant_message_id:
        return reject("assistant_message_missing")

    user = db.get(User, update.user_id)
    if (
        user is None
        or user.role != "user"
        or not user.is_active
        or user.status != "active"
    ):
        return reject("telegram_user_not_active")

    link = (
        db.query(TelegramCentralLink)
        .filter(
            TelegramCentralLink.user_id == update.user_id,
            TelegramCentralLink.telegram_user_id == update.telegram_user_id,
            TelegramCentralLink.is_active.is_(True),
        )
        .first()
    )
    if link is None:
        return reject("telegram_link_identity_mismatch")

    assistant_message = db.get(Message, update.assistant_message_id)
    if (
        assistant_message is None
        or assistant_message.role != "assistant"
    ):
        return reject("assistant_message_not_found_or_invalid")

    conversation = db.get(Conversation, assistant_message.conversation_id)
    if (
        conversation is None
        or conversation.user_id != update.user_id
    ):
        return reject("assistant_message_owner_mismatch")

    stored_result = db.get(
        TokenReservationResult,
        update.reservation_id,
    )
    reservation = db.get(TokenReservation, update.reservation_id)

    if (
        stored_result is None
        or reservation is None
        or stored_result.user_id != update.user_id
        or reservation.user_id != update.user_id
        or stored_result.assistant_message_id != update.assistant_message_id
        or reservation.reference != f"telegram-central:{update.update_id}"
        or stored_result.response_content != assistant_message.content
        or stored_result.skill != assistant_message.skill
    ):
        return reject("reservation_identity_mismatch")

    if (
        stored_result.status != "settled"
        or reservation.status != "completed"
    ):
        return reject("reservation_not_settled")

    token_values = (
        stored_result.prompt_tokens,
        stored_result.completion_tokens,
        stored_result.total_tokens,
        reservation.actual_tokens,
    )
    if any(
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        for value in token_values
    ):
        return reject("invalid_token_usage")

    if (
        stored_result.total_tokens
        != stored_result.prompt_tokens + stored_result.completion_tokens
        or reservation.actual_tokens != stored_result.total_tokens
    ):
        return reject("token_usage_mismatch")

    # Permintaan dokumen harus memiliki metadata lengkap dan format yang valid.
    document_requested = update.document_delivery_status != "not_required"

    if document_requested:
        if (
            not update.generated_file_id
            or not update.document_format
            or not update.document_title
        ):
            return reject("document_metadata_missing")

        if update.document_delivery_status == "sending":
            # Proses sebelumnya mungkin mati setelah Telegram menerima file.
            # Jangan pernah mengirim ulang secara otomatis dalam keadaan ini.
            return reject("telegram_document_delivery_uncertain")

        if update.document_delivery_status == "sent":
            update.status = "completed"
            update.error_code = None
            update.completed_at = _utc_now()
            db.commit()
            return True

        if update.document_delivery_status == "failed":
            update.status = "completed"
            update.error_code = "document_generation_or_delivery_failed"
            update.completed_at = _utc_now()
            db.commit()
            return True

        if update.document_delivery_status != "pending":
            return reject("invalid_document_delivery_status")

        # Generator idempotent: jika webhook diproses ulang setelah dokumen
        # dibuat, file yang sama akan digunakan, bukan dibuat berulang.
        try:
            if (
                not isinstance(update.document_content, str)
                or not update.document_content.strip()
            ):
                raise ValueError("document_content_missing_or_invalid")

            create_user_document(
                db=db,
                user=user,
                title=update.document_title,
                body_text=update.document_content,
                file_format=update.document_format,
                file_id=update.generated_file_id,
            )
        except (WorkspaceError, ValueError, OSError, RuntimeError):
            # Jangan tandai failed sebelum jawaban AI berhasil dikirim.
            # Jika pengiriman teks gagal, webhook retry masih dapat mencoba
            # kembali pembuatan/pengiriman jawaban.
            failure_notice = (
                "\\n\\n⚠️ Dokumen belum berhasil dibuat atau disimpan. "
                "Jawaban AI tetap tersedia di atas. Silakan coba kembali "
                "atau hubungi Admin jika masalah berlanjut."
            )
            if update.response_sent_at is None:
                sent = await _send_message(
                    chat_id,
                    assistant_message.content + failure_notice,
                )

                if sent is None:
                    return reject("telegram_delivery_uncertain")

                if sent is False:
                    update.status = "reply_pending"
                    update.error_code = "telegram_send_failed"
                    db.commit()
                    return False

                update.response_sent_at = _utc_now()

            update.document_delivery_status = "failed"
            update.status = "completed"
            update.error_code = "document_generation_failed"
            update.completed_at = _utc_now()
            db.commit()
            return True

    # Teks jawaban hanya dikirim jika belum pernah dikonfirmasi terkirim.
    if update.response_sent_at is None:
        sent = await _send_message(chat_id, assistant_message.content)

        if sent is None:
            update.status = "needs_review"
            update.error_code = "telegram_delivery_uncertain"
            db.commit()
            return False

        if sent is False:
            update.status = "reply_pending"
            update.error_code = "telegram_send_failed"
            db.commit()
            return False

        update.response_sent_at = _utc_now()
        db.commit()

    if document_requested:
        # Simpan status sebelum network call. Jika proses mati saat pengiriman,
        # webhook retry tidak akan mengirim lampiran kedua kali.
        update.document_delivery_status = "sending"
        db.commit()

        try:
            document_sent = await send_generated_document(
                chat_id=chat_id,
                user_id=update.user_id,
                file_id=update.generated_file_id,
                file_format=update.document_format,
                title=update.document_title,
            )
        except WorkspaceError:
            document_sent = False

        if document_sent is None:
            return reject("telegram_document_delivery_uncertain")

        if document_sent is False:
            update.document_delivery_status = "failed"
            db.commit()

            warning_sent = await _send_message(
                chat_id,
                "⚠️ Jawaban AI berhasil dikirim, tetapi lampiran dokumen "
                "gagal dikirim oleh Telegram. Dokumen mungkin masih tersedia "
                "di menu 📁 File Saya.",
            )

            if warning_sent is None:
                return reject("telegram_document_warning_uncertain")

            if warning_sent is False:
                return reject("telegram_document_warning_failed")

            update.status = "completed"
            update.error_code = "telegram_document_send_failed"
            update.completed_at = _utc_now()
            db.commit()
            return True

        update.document_delivery_status = "sent"

    update.status = "completed"
    update.error_code = None
    update.completed_at = _utc_now()
    db.commit()
    return True


@router.get("/status")
def central_telegram_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (
        current_user.role != "user"
        or not current_user.is_active
        or current_user.status != "active"
    ):
        raise HTTPException(
            status_code=403,
            detail="Bot Telegram pusat hanya tersedia untuk akun pengguna aktif.",
        )

    link = (
        db.query(TelegramCentralLink)
        .filter(TelegramCentralLink.user_id == current_user.id)
        .first()
    )

    return {
        "bot_username": OMAI_TELEGRAM_BOT_USERNAME,
        "linked": bool(link and link.is_active and link.telegram_user_id),
        "telegram_username": (
            link.telegram_username
            if link and link.is_active
            else None
        ),
        "linked_at": (
            link.linked_at.isoformat()
            if link and link.is_active and link.linked_at
            else None
        ),
    }


@router.post("/link-code")
def create_central_link_code(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (
        current_user.role != "user"
        or not current_user.is_active
        or current_user.status != "active"
    ):
        raise HTTPException(
            status_code=403,
            detail="Bot Telegram pusat hanya tersedia untuk akun pengguna aktif.",
        )

    if not OMAI_TELEGRAM_BOT_USERNAME:
        raise HTTPException(
            status_code=503,
            detail="Username bot pusat belum dikonfigurasi.",
        )

    link = (
        db.query(TelegramCentralLink)
        .filter(TelegramCentralLink.user_id == current_user.id)
        .with_for_update()
        .first()
    )

    if link and link.is_active and link.telegram_user_id:
        raise HTTPException(
            status_code=409,
            detail="Akun OM.AI sudah terhubung ke Telegram.",
        )

    code = secrets.token_urlsafe(24)
    expires_at = _utc_now() + timedelta(minutes=LINK_CODE_MINUTES)

    if link is None:
        link = TelegramCentralLink(
            user_id=current_user.id,
            is_active=False,
        )
        db.add(link)

    link.link_code_hash = _hash_code(code)
    link.link_code_expires_at = expires_at

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Permintaan kode pengaitan berbenturan. Silakan buat kode baru.",
        )

    deep_link = (
        f"https://t.me/{OMAI_TELEGRAM_BOT_USERNAME}"
        f"?start={quote(code, safe='')}"
    )

    return {
        "bot_username": OMAI_TELEGRAM_BOT_USERNAME,
        "link": deep_link,
        "expires_in_seconds": LINK_CODE_MINUTES * 60,
    }


@router.post("/unlink")
def unlink_central_telegram(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if (
        current_user.role != "user"
        or not current_user.is_active
        or current_user.status != "active"
    ):
        raise HTTPException(
            status_code=403,
            detail="Bot Telegram pusat hanya tersedia untuk akun pengguna aktif.",
        )

    link = (
        db.query(TelegramCentralLink)
        .filter(TelegramCentralLink.user_id == current_user.id)
        .with_for_update()
        .first()
    )

    if link is None:
        return {"ok": True, "linked": False}

    # Hanya lepaskan identitas Telegram dan kode pengaitan.
    # conversation_id serta seluruh pesan tetap dipertahankan.
    link.telegram_user_id = None
    link.telegram_username = None
    link.linked_at = None
    link.link_code_hash = None
    link.link_code_expires_at = None
    link.is_active = False
    db.commit()

    return {"ok": True, "linked": False}



async def _handle_filesaya_command(
    command_text: str,
    db: Session,
    user: User,
    chat_id: int,
    update: TelegramCentralUpdate,
) -> bool:
    """Tangani perintah File Saya; True jika perintah dikenali."""
    raw = command_text.strip()
    parts = raw.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower() if parts else ""
    argument = parts[1].strip() if len(parts) == 2 else ""

    supported = {
        "/daftar_file", "/cari_file", "/folder", "/isi_folder",
        "/buat_folder", "/storage", "/baca", "/unduh",
        "/rename_file", "/rename_folder", "/hapus_file",
        "/konfirmasi_hapus", "/hapus_folder",
    }
    if command not in supported:
        return False

    update.user_id = user.id
    db.commit()
    reply = ""

    try:
        if command == "/daftar_file":
            reply = list_user_files(user.id)

        elif command == "/cari_file":
            reply = search_user_files(user.id, argument) if argument else (
                "Format: /cari_file kata_kunci"
            )

        elif command == "/folder":
            reply = list_user_folders(user.id)

        elif command == "/isi_folder":
            reply = list_user_folders(user.id, argument) if argument else (
                "Format: /isi_folder nama_folder"
            )

        elif command == "/buat_folder":
            reply = create_user_folder(user.id, argument) if argument else (
                "Format: /buat_folder nama_folder"
            )

        elif command == "/storage":
            reply = storage_user_info(db, user)

        elif command == "/baca":
            if not argument:
                reply = "Format: /baca ID_FILE"
            else:
                extension = get_user_file_extension(user.id, argument)
                if extension in {".jpg", ".jpeg", ".png"}:
                    await send_user_image_preview(chat_id, user.id, argument)
                    reply = f"Preview gambar untuk file ID {argument}."
                else:
                    reply = preview_user_file(user.id, argument)

        elif command == "/unduh":
            if not argument:
                reply = "Format: /unduh ID_FILE"
            else:
                await send_user_file(chat_id, user.id, argument)
                reply = f"File dengan ID {argument} telah dikirim."

        elif command == "/rename_file":
            if "|" not in argument:
                reply = "Format: /rename_file ID_FILE|nama_baru.ext"
            else:
                file_id, new_name = (part.strip() for part in argument.split("|", 1))
                reply = rename_user_file(user.id, file_id, new_name)

        elif command == "/rename_folder":
            if "|" not in argument:
                reply = "Format: /rename_folder path_folder|nama_baru"
            else:
                folder_path, new_name = (
                    part.strip() for part in argument.split("|", 1)
                )
                reply = rename_user_folder(user.id, folder_path, new_name)

        elif command == "/hapus_file":
            if not argument:
                reply = "Format: /hapus_file ID_FILE"
            else:
                reply = (
                    f"⚠️ Konfirmasi penghapusan file ID {argument}.\n"
                    f"Jika yakin, kirim: /konfirmasi_hapus {argument}\n"
                    "File hanya dihapus setelah perintah konfirmasi tersebut."
                )

        elif command == "/konfirmasi_hapus":
            reply = delete_user_file(user.id, argument) if argument else (
                "Format: /konfirmasi_hapus ID_FILE"
            )

        elif command == "/hapus_folder":
            reply = delete_user_folder(user.id, argument) if argument else (
                "Format: /hapus_folder path_folder"
            )

    except WorkspaceError as exc:
        reply = str(exc)
    except (ValueError, OSError):
        reply = "Operasi File Saya gagal. Periksa kembali ID, nama, atau lokasi file."

    update.status = "completed"
    update.error_code = None
    update.completed_at = _utc_now()
    db.commit()

    if reply:
        await _send_message(chat_id, reply, reply_markup=_workspace_menu_markup())
    return True


@router.post("/webhook")
async def central_telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
    db: Session = Depends(get_db),
):
    if (
        not OMAI_TELEGRAM_WEBHOOK_SECRET
        or not x_telegram_bot_api_secret_token
        or not secrets.compare_digest(
            x_telegram_bot_api_secret_token,
            OMAI_TELEGRAM_WEBHOOK_SECRET,
        )
    ):
        raise HTTPException(status_code=403, detail="Webhook secret tidak valid.")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Payload Telegram tidak valid.")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload harus berupa objek.")

    update_id = payload.get("update_id")
    if isinstance(update_id, bool) or not isinstance(update_id, int):
        raise HTTPException(status_code=400, detail="Update ID tidak valid.")

    message = payload.get("message")
    if not isinstance(message, dict):
        return {"ok": True, "ignored": True}

    sender = message.get("from")
    telegram_chat = message.get("chat")
    if not isinstance(sender, dict) or not isinstance(telegram_chat, dict):
        return {"ok": True, "ignored": True}

    sender_id = sender.get("id")
    chat_id = telegram_chat.get("id")
    message_id = message.get("message_id")

    private_chat = (
        telegram_chat.get("type") == "private"
        and isinstance(sender_id, int)
        and not isinstance(sender_id, bool)
        and isinstance(chat_id, int)
        and not isinstance(chat_id, bool)
        and sender_id == chat_id
    )

    update, duplicate = _record_update(
        db=db,
        update_id=update_id,
        telegram_user_id=sender_id if isinstance(sender_id, int) else None,
        telegram_message_id=message_id if isinstance(message_id, int) else None,
    )

    if update is None:
        return {"ok": True, "duplicate": True}

    if duplicate:
        if (
            update.status == "reply_pending"
            and private_chat
            and update.telegram_user_id == sender_id
        ):
            delivered = await _deliver_saved_reply(db, update, chat_id)
            return {
                "ok": True,
                "duplicate": True,
                "reply_retried": delivered,
            }

        # Status processing tidak pernah memicu AI kedua.
        return {
            "ok": True,
            "duplicate": True,
            "status": update.status,
        }

    if not private_chat:
        update.status = "completed"
        update.error_code = "ignored_non_private_message"
        update.completed_at = _utc_now()
        db.commit()
        return {"ok": True, "ignored": True}

    text = message.get("text")
    if not isinstance(text, str):
        text = ""

    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()

    if command == "/start":
        code = parts[1].strip() if len(parts) == 2 else ""

        if not code or len(code) > 64:
            existing_link = (
                db.query(TelegramCentralLink)
                .filter(
                    TelegramCentralLink.telegram_user_id == sender_id,
                    TelegramCentralLink.is_active.is_(True),
                )
                .first()
            )
            linked_user = db.get(User, existing_link.user_id) if existing_link else None

            if (
                existing_link is not None
                and linked_user is not None
                and linked_user.role == "user"
                and linked_user.is_active
                and linked_user.status == "active"
            ):
                update.user_id = linked_user.id
                update.status = "completed"
                update.error_code = None
                update.completed_at = _utc_now()
                db.commit()
                await _send_workspace_menu(chat_id)
                return {"ok": True, "linked": True, "menu": True}

            update.status = "completed"
            update.error_code = "start_without_valid_code"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(
                chat_id,
                "Untuk menghubungkan akun, buat kode pengaitan dari aplikasi ONE MASTER AI terlebih dahulu.",
            )
            return {"ok": True, "linked": False}

        supplied_hash = _hash_code(code)
        link = (
            db.query(TelegramCentralLink)
            .filter(TelegramCentralLink.link_code_hash == supplied_hash)
            .with_for_update()
            .first()
        )

        now = _utc_now()
        user = db.get(User, link.user_id) if link else None

        existing_telegram_link = (
            db.query(TelegramCentralLink)
            .filter(
                TelegramCentralLink.telegram_user_id == sender_id,
                TelegramCentralLink.is_active.is_(True),
            )
            .first()
        )

        valid = (
            link is not None
            and existing_telegram_link is None
            and link.telegram_user_id is None
            and not link.is_active
            and link.link_code_expires_at is not None
            and link.link_code_expires_at > now
            and user is not None
            and user.role == "user"
            and user.is_active
            and user.status == "active"
        )

        if valid:
            link.telegram_user_id = sender_id
            username = sender.get("username")
            link.telegram_username = username[:64] if isinstance(username, str) else None
            link.linked_at = now
            link.is_active = True
            link.link_code_hash = None
            link.link_code_expires_at = None
            update.user_id = user.id
            update.status = "completed"
            update.completed_at = now

            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                db.refresh(update)
                update.status = "failed"
                update.error_code = "telegram_identity_conflict"
                update.completed_at = _utc_now()
                db.commit()
                await _send_message(
                    chat_id,
                    "Akun Telegram ini tidak dapat dihubungkan. Periksa apakah Telegram sudah terhubung ke akun OM.AI lain.",
                )
                return {"ok": True, "linked": False}

            await _send_workspace_menu(
                chat_id,
                "Akun Telegram berhasil terhubung dengan ONE MASTER AI.\n"
                "Selamat datang di AI Workspace OM.AI.",
            )
            return {"ok": True, "linked": True, "menu": True}

        update.status = "completed"
        update.error_code = "invalid_or_expired_link_code"
        update.completed_at = now
        db.commit()
        await _send_message(
            chat_id,
            "Kode pengaitan tidak valid atau sudah kedaluwarsa. Buat kode baru dari aplikasi OM.AI.",
        )
        return {"ok": True, "linked": False}

    link = (
        db.query(TelegramCentralLink)
        .filter(
            TelegramCentralLink.telegram_user_id == sender_id,
            TelegramCentralLink.is_active.is_(True),
        )
        .first()
    )

    if link is None:
        update.status = "completed"
        update.error_code = "telegram_account_not_linked"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Akun Telegram belum terhubung. Hubungkan melalui aplikasi ONE MASTER AI terlebih dahulu.",
        )
        return {"ok": True, "ignored": True}

    user = db.get(User, link.user_id)
    if (
        user is None
        or user.role != "user"
        or not user.is_active
        or user.status != "active"
    ):
        update.user_id = link.user_id
        update.status = "failed"
        update.error_code = "omai_user_inactive"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(chat_id, "Akun ONE MASTER AI Anda tidak aktif.")
        return {"ok": True, "ignored": True}

    # Semua operasi file dilakukan setelah akun tertaut dan status user tervalidasi.
    document = message.get("document")
    if isinstance(document, dict):
        update.user_id = user.id
        db.commit()
        try:
            reply = await upload_telegram_document(db, user, document)
            update.status = "completed"
            update.error_code = None
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(
                chat_id, reply, reply_markup=_workspace_menu_markup()
            )
            return {"ok": True, "file_uploaded": True}
        except WorkspaceError as exc:
            update.status = "completed"
            update.error_code = "file_upload_rejected"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(chat_id, str(exc))
            return {"ok": True, "file_uploaded": False}
        except Exception:
            db.rollback()
            db.refresh(update)
            update.status = "needs_review"
            update.error_code = "file_upload_uncertain"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(
                chat_id,
                "Unggahan belum dapat dipastikan selesai. Periksa daftar File Saya sebelum mengirim ulang.",
            )
            return {"ok": True, "file_uploaded": False}

    photos = message.get("photo")
    if isinstance(photos, list) and photos:
        valid_photos = [
            item for item in photos
            if isinstance(item, dict) and item.get("file_id")
        ]
        if not valid_photos:
            update.user_id = user.id
            update.status = "completed"
            update.error_code = "invalid_telegram_photo"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(chat_id, "Foto Telegram tidak valid.")
            return {"ok": True, "file_uploaded": False}

        largest_photo = max(
            valid_photos,
            key=lambda item: (
                int(item.get("file_size") or 0),
                int(item.get("width") or 0) * int(item.get("height") or 0),
            ),
        )
        photo_document = {
            "file_id": largest_photo["file_id"],
            "file_size": largest_photo.get("file_size"),
            "file_name": (
                "telegram_photo_"
                f"{largest_photo.get('file_unique_id') or 'image'}.jpg"
            ),
        }

        update.user_id = user.id
        db.commit()
        try:
            reply = await upload_telegram_document(db, user, photo_document)
            update.status = "completed"
            update.error_code = None
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(
                chat_id, reply, reply_markup=_workspace_menu_markup()
            )
            return {"ok": True, "file_uploaded": True, "photo": True}
        except WorkspaceError as exc:
            update.status = "completed"
            update.error_code = "file_upload_rejected"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(chat_id, str(exc))
            return {"ok": True, "file_uploaded": False, "photo": True}
        except Exception:
            db.rollback()
            db.refresh(update)
            update.status = "needs_review"
            update.error_code = "file_upload_uncertain"
            update.completed_at = _utc_now()
            db.commit()
            await _send_message(
                chat_id,
                "Unggahan foto belum dapat dipastikan selesai. "
                "Periksa daftar File Saya sebelum mengirim ulang.",
            )
            return {"ok": True, "file_uploaded": False, "photo": True}

    media_keys = (
        "photo", "video", "animation", "audio", "voice",
        "video_note", "sticker",
    )
    if any(key in message for key in media_keys):
        update.user_id = user.id
        update.status = "completed"
        update.error_code = "unsupported_telegram_media"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Jenis lampiran ini belum didukung. File Saya menerima PDF, DOCX, DOC, "
            "TXT, MD, XLSX, XLS, PPTX, PPT, JPG, JPEG, dan PNG maksimal 5 MB.",
        )
        return {"ok": True, "unsupported_media": True}

    if not text.strip():
        update.user_id = user.id
        update.status = "completed"
        update.error_code = "unsupported_message_type"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Kirim teks untuk Chat AI, atau lampirkan PDF, DOCX, DOC, TXT, MD, "
            "XLSX, XLS, PPTX, PPT, JPG, JPEG, atau PNG untuk File Saya (maksimal 5 MB).",
        )
        return {"ok": True, "ignored": True}

    menu_choice = text.strip()

    if command == "/menu" or menu_choice in {"🏠 Menu Utama", "Menu Utama"}:
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()
        await _send_workspace_menu(chat_id)
        return {"ok": True, "menu": True}

    if menu_choice == "💬 Chat AI":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Mode Chat AI aktif. Silakan kirim pertanyaan atau instruksi Anda.\n"
            "Gunakan tombol 🏠 Menu Utama untuk kembali ke navigasi layanan.",
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "chat_ready": True}

    workspace_placeholders = {
        "📚 Riwayat Chat": "Riwayat Chat sedang disiapkan untuk integrasi Telegram.",
        "📁 File Saya": "Layanan File Saya sedang disiapkan untuk integrasi Telegram.",
        "📄 Buat Dokumen": (
            "📄 BUAT DOKUMEN OM.AI\n\n"
            "Kirim instruksi dokumen yang ingin dibuat. "
            "AI akan menyusun isinya dan mengirimkan file ke Telegram.\n\n"
            "Format default: Word (.docx).\n"
            "Format lain yang didukung: PDF, DOC, TXT, MD, "
            "Excel (XLS/XLSX), PowerPoint (PPT/PPTX), JPG, JPEG, PNG.\n\n"
            "Contoh:\n"
            "• Buatkan laporan kegiatan dalam Word\n"
            "• Buatkan proposal penelitian dalam PDF\n"
            "• Buatkan tabel anggaran dalam Excel\n"
            "• Buatkan presentasi dalam PowerPoint\n\n"
            "Anda juga dapat menuliskan judul dengan format: Judul: Nama Dokumen"
        ),
        "🪙 Token & Top Up": "Informasi Token & Top Up sedang disiapkan. Bot Top Up resmi tetap tersedia melalui @OmAIpay_Bot.",
        "👤 Profil & Bantuan": "Menu Profil & Bantuan sedang disiapkan untuk integrasi Telegram.",
    }

    if menu_choice == "📁 File Saya":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            workspace_menu_text(),
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "workspace_section": "files"}

    if await _handle_filesaya_command(text, db, user, chat_id, update):
        return {"ok": True, "workspace_section": "files"}

    if menu_choice in workspace_placeholders:
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            workspace_placeholders[menu_choice],
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "workspace_section": "coming_soon"}

    if len(text.strip()) > 8000:
        update.status = "completed"
        update.error_code = "telegram_message_too_long"
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Pesan terlalu panjang. Batas pesan untuk satu permintaan adalah 8.000 karakter.",
        )
        return {"ok": True, "processed": False, "reason": "message_too_long"}

    update.user_id = user.id

    document_request = _detect_document_request(text.strip())
    if document_request is not None:
        document_format, document_title = document_request
        update.document_format = document_format
        update.document_title = document_title
        update.generated_file_id = hashlib.sha256(
            f"{user.id}:{update.update_id}".encode("utf-8")
        ).hexdigest()[:12]
        update.document_delivery_status = "pending"

    db.commit()

    try:
        # Memanggil alur chat yang sama dengan aplikasi web.
        # Tidak membuat sistem kuota atau settlement Telegram tersendiri.
        result = _chat_impl(
            ChatRequest(
                message=text.strip(),
                model_id="default",
                conversation_id=link.conversation_id,
            ),
            current_user=user,
            db=db,
            reservation_reference=f"telegram-central:{update_id}",
            document_mode=bool(document_request),
        )

        # Simpan identitas jawaban sebelum mencoba mengirim ke Telegram.
        update.assistant_message_id = result.assistant_message_id
        update.reservation_id = result.reservation_id
        update.document_content = (
            result.document_content if document_request is not None else None
        )
        link.conversation_id = result.conversation_id
        update.status = "reply_pending"
        update.error_code = None
        db.commit()

    except HTTPException as exc:
        db.rollback()
        db.refresh(update)
        update.status = "needs_review" if exc.status_code == 503 else "failed"
        update.error_code = (
            "chat_result_uncertain"
            if exc.status_code == 503
            else f"chat_http_{exc.status_code}"
        )
        update.completed_at = _utc_now()
        db.commit()
        await _send_message(
            chat_id,
            "Permintaan belum dapat diselesaikan. Silakan periksa status akun atau hubungi Admin jika masalah berlanjut.",
        )
        return {"ok": True, "processed": False}

    except Exception:
        db.rollback()
        db.refresh(update)
        update.status = "needs_review"
        update.error_code = "chat_processing_uncertain"
        update.completed_at = _utc_now()
        db.commit()
        return {"ok": True, "processed": False}

    delivered = await _deliver_saved_reply(db, update, chat_id)
    return {"ok": True, "processed": True, "reply_sent": delivered}
