import asyncio
"""Telegram bot pusat ONE MASTER AI.

Router ini berdampingan dengan sistem Telegram per-user yang lama.
Tidak mengubah webhook, token, atau data bot lain.
"""

import hashlib
import json
import secrets
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.api.routes_auth import build_user_info
from app.api.routes_chat import ChatRequest, _chat_impl
from app.api.routes_automation import (
    AutomationExecuteRequest,
    list_available_workflows,
    execute_automation_workflow,
    list_my_executions,
)
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
from app.models.subscription import (
    SubscriptionPlan,
    TokenWallet,
    UserSubscription,
)
from app.models.topup import TopUpRequest
from app.services.quota_service import WIT, daily_token_usage
from app.services.token_service import wit_now
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


from app.services.document_title_helper import resolve_document_title

router = APIRouter(
    prefix="/api/v1/telegram-central",
    tags=["telegram-central"],
)

LINK_CODE_MINUTES = 15


def _is_pure_export_command(message_text: str) -> bool:
    """Deteksi perintah ekspor murni (mis. 'Buat dalam pdf') tanpa instruksi ubah isi."""
    normalized = (message_text or "").lower().strip()
    if not normalized or len(normalized) > 80:
        return False
    # Jika ada instruksi pengolahan ulang, tetap serahkan ke LLM.
    if re.search(
        r"\b(ringkas|rangkum|perbaiki|revisi|ubah isi|tambahkan|hapus|terjemahkan|translate|buat baru|tentang|mengenai|bahas)\b",
        normalized,
    ):
        return False
    filler = {
        "buat", "buatkan", "jadikan", "ubah", "konversi", "export", "ekspor",
        "simpan", "cetak", "kirim", "format", "versi", "dalam", "bentuk", "ke",
        "jadi", "menjadi", "sebagai", "file", "berkas", "dokumen",
        "pdf", "docx", "word", "doc", "excel", "xlsx", "xls",
        "ppt", "pptx", "powerpoint", "markdown", "md", "txt", "text",
        "jpg", "jpeg", "png", "bikin", "bikinkan", "convert",
        "tolong", "bisa", "coba", "minta", "chat", "isi", "percakapan",
        "di", "atas", "diatas", "tersebut", "tadi", "sebelumnya", "hasil",
        "hasilnya", "jawaban", "review", "ini", "itu", "nya", "dong", "ya",
        "aja", "saja", "please", "yang", "dari", "untuk", "langsung",
    }
    if not re.search(
        r"\b(pdf|docx|word|doc|xlsx|excel|xls|pptx|powerpoint|power\s*point|ppt|markdown|md|txt|text|jpeg|jpg|png)\b",
        normalized,
    ):
        return False
    words = re.findall(r"[a-z0-9]+", normalized)
    if not words:
        return False
    remaining = [w for w in words if w not in filler]
    return len(remaining) == 0


def _utc_now() -> datetime:
    return datetime.utcnow()


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _automatic_upload_context_file_id(user_id: str, message: str) -> str | None:
    """Pilih unggahan terbaru milik user hanya saat pesan jelas merujuk file."""
    reference = re.search(
        r"\b(draft|draf|dokumen|berkas|file|lampiran|unggahan|upload|"
        r"diunggah|terunggah|gambar|foto|image|photo|screenshot|tangkapan|ilustrasi|diagram|grafik|bagan|review|reviu|ulas(?:an)?|koreksi|periksa|cek|analisis|analisa|ringkas(?:an)?|rangkum(?:an)?|baca|bedah|evaluasi|revisi|perbaiki|parafrase|terjemah(?:kan)?|skripsi|proposal|tesis|disertasi|makalah|naskah|jurnal|laporan)\b",
        str(message or ""),
        flags=re.IGNORECASE,
    )
    if not reference:
        return None

    try:
        from app.services.workspace_service import list_files

        files = list_files(user_id, limit=100)
        uploads = [
            item for item in files
            if not str(item.get("folder_path") or "").strip().lower().startswith("hasil ai")
        ]
        uploads.sort(
            key=lambda item: float(item.get("modified_at") or 0.0),
            reverse=True,
        )

        image_only_query = bool(
            re.search(r"\b(gambar|foto|image|photo|screenshot|tangkapan)\b", str(message or ""), flags=re.IGNORECASE)
            and not re.search(r"\b(skripsi|proposal|tesis|disertasi|makalah|naskah|jurnal|laporan|dokumen|pdf|docx|word|excel|xlsx|ppt|pptx)\b", str(message or ""), flags=re.IGNORECASE)
        )

        for item in uploads:
            file_id = str(item.get("id") or "")
            if not file_id:
                continue

            try:
                from app.services.workspace_service import read_file, WorkspaceError
                contents = read_file(user_id, file_id, max_chars=30000)
                ext = str(contents.get("extension") or "").lower()
                if ext in {".jpg", ".jpeg", ".png"}:
                    return file_id
                if not image_only_query and str(contents.get("content") or "").strip():
                    return file_id
            except (WorkspaceError, OSError, ValueError):
                continue

    except Exception:
        return None

    return None


def _detect_document_request(message: str) -> tuple[str, str] | None:
    """Deteksi permintaan pembuatan dokumen dan format output."""
    value = str(message or "").strip()
    if not value:
        return None

    intent = re.search(
        r"\b(buat(?:kan)?|bikin|susun|siapkan|hasilkan|jadikan(?:kan)?|"
        r"jadi(?:kan)?|ekspor|export|convert|konversi|ubah(?:kan)?|"
        r"simpan(?:kan)?|review|reviu|ulas|analisis|analisa|ringkas|rangkum|koreksi|evaluasi|bedah|rekap|tulis(?:kan)?)\b",
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

    explicit_format = re.search(
        r"\b(pdf|docx|word|doc|xlsx|excel|xls|pptx|powerpoint|power\s*point|ppt|markdown|md|txt|jpeg|jpg|png)\b",
        value,
        flags=re.IGNORECASE,
    )

    pure_export = bool(explicit_format) and _is_pure_export_command(value)
    if not pure_export and (not intent or not (document or format_only or explicit_format)):
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
        (r"\b(pptx|ppt|powerpoint|power\s*point)\b", "pptx"),
        (r"\bmarkdown\b|\bmd\b", "md"),
        (r"\btxt\b|\btext\b", "txt"),
        (r"\bjpeg\b", "jpeg"),
        (r"\bjpg\b", "jpg"),
        (r"\bpng\b", "png"),
        (r"\b(presentasi|slide|salindia)\b", "pptx"),
        (r"\bspreadsheet\b", "xlsx"),
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
            [{"text": "⚙️ Automation"}],
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
        "⚙️ Automation — jalankan workflow otomatis\n"
        "🪙 Token & Top Up — kuota dan pengisian saldo\n"
        "👤 Profil & Bantuan — informasi akun dan panduan"
    )
    return await _send_message(
        chat_id,
        menu_text,
        reply_markup=_workspace_menu_markup(),
    )


def _split_markdown_chunks(text: str, limit: int = 3200) -> list[str]:
    raw = str(text or "")
    if len(raw) <= limit:
        return [raw]
    chunks: list[str] = []
    current = ""
    for line in raw.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
        else:
            if current:
                chunks.append(current)
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    if current:
        chunks.append(current)
    return chunks or [""]


def _markdown_to_telegram_html(text: str) -> str:
    import html as _html
    import re as _re

    src = str(text or "")
    stash: list[str] = []

    def _put(html_snippet: str) -> str:
        token = f"\x00CODE{len(stash)}\x00"
        stash.append(html_snippet)
        return token

    src = _re.sub(
        r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```",
        lambda m: _put(f"<pre>{_html.escape(m.group(1).strip(), quote=False)}</pre>"),
        src,
    )
    src = _re.sub(
        r"`([^`\n]+)`",
        lambda m: _put(f"<code>{_html.escape(m.group(1), quote=False)}</code>"),
        src,
    )

    src = _html.escape(src, quote=False)
    src = _re.sub(r"(?m)^\s*\|[\s:|-]+\|\s*$\n?", "", src)

    def _table_row(m: _re.Match) -> str:
        cells = [c.strip() for c in m.group(1).strip("|").split("|") if c.strip()]
        if not cells:
            return ""
        if len(cells) == 2:
            return f"• <b>{cells[0]}</b>: {cells[1]}"
        return "• " + " | ".join(cells)

    src = _re.sub(r"(?m)^\s*(\|.+\|)\s*$", _table_row, src)
    src = _re.sub(
        r"(?m)^\s{0,3}#{1,6}\s+(.+)$",
        lambda m: f"\n<b>{m.group(1).strip().strip('*').strip()}</b>",
        src,
    )
    src = _re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "──────────", src)
    src = _re.sub(r"(?m)^[ \t]{1,}[-*•]\s+(.+)$", r"  ◦ \1", src)
    src = _re.sub(r"(?m)^[-*]\s+(.+)$", r"• \1", src)
    src = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", src)
    src = _re.sub(r"__(.+?)__", r"<b>\1</b>", src)
    src = _re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", src)
    src = _re.sub(r"\n{3,}", "\n\n", src).strip()

    for idx, snippet in enumerate(stash):
        src = src.replace(f"\x00CODE{idx}\x00", snippet)
    return src


def _filesaya_delete_inline_markup(user_id: str, limit: int = 10) -> dict | None:
    """Buat inline keyboard tombol Hapus per file (maksimal 10 file terbaru agar rapi di Telegram)."""
    try:
        from app.services.workspace_service import list_files

        files = list_files(user_id, limit=100)
        rows = []
        for item in files[:limit]:
            fid = str(item.get("id") or "").strip()
            name = str(item.get("name") or item.get("filename") or fid).strip()
            if not fid or len(f"fcdel:{fid}".encode("utf-8")) > 64:
                continue
            label_name = name if len(name) <= 28 else f"{name[:25]}..."
            rows.append([
                {
                    "text": f"🗑️ Hapus {label_name}",
                    "callback_data": f"fdel:{fid}",
                }
            ])
        if not rows:
            return None
        return {"inline_keyboard": rows}
    except Exception:
        return None


async def _send_raw_telegram_method(method: str, payload: dict) -> dict | None:
    """Panggil method Telegram Bot API secara langsung (mis. answerCallbackQuery / sendMessage)."""
    import httpx

    if not OMAI_TELEGRAM_BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/{method}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                return resp.json()
            return None
    except httpx.HTTPError:
        return None


async def _handle_filesaya_callback(
    callback_query: dict,
    db: Session,
) -> dict | None:
    """Tangani klik tombol inline Hapus pada menu File Saya."""
    cb_id = str(callback_query.get("id") or "")
    data = str(callback_query.get("data") or "")
    sender = callback_query.get("from") or {}
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}

    if not data.startswith(("fdel:", "fcdel:", "fcancel:")):
        return None

    sender_id = sender.get("id")
    chat_id = chat.get("id")
    if (
        chat.get("type") != "private"
        or not isinstance(sender_id, int)
        or isinstance(sender_id, bool)
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or sender_id != chat_id
    ):
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Hanya tersedia di chat pribadi.", "show_alert": True},
        )
        return {"ok": True, "ignored": True}

    link = (
        db.query(TelegramCentralLink)
        .filter(
            TelegramCentralLink.telegram_user_id == sender_id,
            TelegramCentralLink.is_active.is_(True),
        )
        .first()
    )
    user = db.get(User, link.user_id) if link and link.user_id else None
    if not link or not user or not user.is_active or user.status != "active":
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Akun Telegram belum terhubung.", "show_alert": True},
        )
        return {"ok": True, "unlinked": True}

    if data.startswith("fcancel:"):
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Penghapusan dibatalkan."},
        )
        markup = _filesaya_delete_inline_markup(user.id)
        await _send_raw_telegram_method(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": list_user_files(user.id),
                **({"reply_markup": markup} if markup else {}),
            },
        )
        return {"ok": True, "cancelled": True}

    if data.startswith("fdel:"):
        fid = data.split(":", 1)[1].strip()
        fname = fid
        try:
            from app.services.workspace_service import list_files
            for item in list_files(user.id, limit=100):
                if str(item.get("id") or "") == fid:
                    fname = str(item.get("name") or fid)
                    break
        except Exception:
            pass

        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Konfirmasi hapus file"},
        )
        await _send_raw_telegram_method(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": f"⚠️ Yakin ingin menghapus file <b>{fname}</b>?",
                "parse_mode": "HTML",
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": "✅ Ya, Hapus", "callback_data": f"fcdel:{fid}"},
                        {"text": "❌ Batal", "callback_data": f"fcancel:{fid}"},
                    ]]
                },
            },
        )
        return {"ok": True, "confirmation_sent": True}

    fid = data.split(":", 1)[1].strip()
    dummy_update = TelegramCentralUpdate(
        update_id=int(str(callback_query.get("id") or "0").isdigit() and callback_query["id"] or 0),
        telegram_user_id=sender_id,
        telegram_message_id=msg.get("message_id"),
        status="processing",
    )
    await _handle_filesaya_command(f"/hapus_file {fid}", db, user, chat_id, dummy_update)
    await _send_raw_telegram_method(
        "answerCallbackQuery",
        {"callback_query_id": cb_id, "text": "File telah dihapus."},
    )
    markup = _filesaya_delete_inline_markup(user.id)
    await _send_raw_telegram_method(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": list_user_files(user.id),
            **({"reply_markup": markup} if markup else {}),
        },
    )
    return {"ok": True, "deleted": True}


async def _send_message(
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
) -> bool | None:
    """Kirim pesan melalui bot pusat dengan format Telegram HTML rapi."""
    import html as _html
    import re as _re

    if not OMAI_TELEGRAM_BOT_TOKEN:
        return False

    chunks = _split_markdown_chunks(text, limit=3200)
    sent_any = False

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            for idx, raw_chunk in enumerate(chunks):
                html_chunk = _markdown_to_telegram_html(raw_chunk)
                use_html = True
                payload_data: dict = {
                    "chat_id": chat_id,
                    "text": html_chunk,
                    "parse_mode": "HTML",
                }
                if reply_markup is not None and len(chunks) == 1:
                    payload_data["reply_markup"] = reply_markup

                delivered = False
                for attempt in range(3):
                    try:
                        response = await client.post(
                            f"https://api.telegram.org/bot{OMAI_TELEGRAM_BOT_TOKEN}/sendMessage",
                            json=payload_data,
                        )
                        if response.status_code == 200:
                            payload = response.json()
                            if isinstance(payload, dict) and payload.get("ok"):
                                delivered = True
                                break
                            print(f"[telegram] sendMessage not ok (chunk {idx}): {payload}", flush=True)
                            return None if sent_any else False

                        if response.status_code == 400 and use_html:
                            use_html = False
                            plain_text = _html.unescape(_re.sub(r"<[^>]+>", "", html_chunk))
                            payload_data = {"chat_id": chat_id, "text": plain_text[:4000]}
                            if reply_markup is not None and len(chunks) == 1:
                                payload_data["reply_markup"] = reply_markup
                            continue

                        print(f"[telegram] sendMessage HTTP {response.status_code} (chunk {idx}): {response.text[:200]}", flush=True)
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        return None if sent_any else False

                    except (httpx.RequestError, ValueError) as exc:
                        print(f"[telegram] sendMessage error (chunk {idx}, attempt {attempt + 1}): {type(exc).__name__}: {exc}", flush=True)
                        if attempt < 2:
                            await asyncio.sleep(1.5 * (attempt + 1))
                            continue
                        return None

                if not delivered:
                    return None if sent_any else False
                sent_any = True

        return True

    except Exception as exc:
        print(f"[telegram] sendMessage unexpected error: {type(exc).__name__}: {exc}", flush=True)
        return None



def _automation_inline_markup(items: list[dict], limit: int = 10) -> dict | None:
    rows = []
    for item in items[:limit]:
        wf_id = str(item.get("id") or "").strip()
        wf_name = str(item.get("name") or "Workflow").strip()
        cb_data = f"arun:{wf_id}"
        if not wf_id or len(cb_data.encode("utf-8")) > 64:
            continue
        label = wf_name if len(wf_name) <= 28 else f"{wf_name[:25]}..."
        rows.append([{"text": f"\u25b6\ufe0f {label}", "callback_data": cb_data}])
    if not rows:
        return None
    rows.append([{"text": "\U0001f9fe Riwayat Eksekusi", "callback_data": "ahist"}])
    return {"inline_keyboard": rows}


async def _handle_automation_callback(
    callback_query: dict,
    db: Session,
) -> dict | None:
    cb_id = str(callback_query.get("id") or "")
    data = str(callback_query.get("data") or "")
    sender = callback_query.get("from") or {}
    msg = callback_query.get("message") or {}
    chat = msg.get("chat") or {}

    if not (data == "ahist" or data.startswith("arun:")):
        return None

    sender_id = sender.get("id")
    chat_id = chat.get("id")
    if (
        chat.get("type") != "private"
        or not isinstance(sender_id, int)
        or isinstance(sender_id, bool)
        or not isinstance(chat_id, int)
        or isinstance(chat_id, bool)
        or sender_id != chat_id
    ):
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Hanya di chat pribadi.", "show_alert": True},
        )
        return {"ok": True, "ignored": True}

    link = (
        db.query(TelegramCentralLink)
        .filter(
            TelegramCentralLink.telegram_user_id == sender_id,
            TelegramCentralLink.is_active.is_(True),
        )
        .first()
    )
    user = db.get(User, link.user_id) if link and link.user_id else None
    if not link or not user or not user.is_active or user.status != "active":
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Akun belum terhubung.", "show_alert": True},
        )
        return {"ok": True, "unlinked": True}

    dummy_update = TelegramCentralUpdate(
        update_id=0,
        telegram_user_id=sender_id,
        telegram_message_id=msg.get("message_id"),
        status="processing",
    )

    if data == "ahist":
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Memuat riwayat..."},
        )
        await _handle_automation_command(
            text="/automation_history",
            command="/automation_history",
            db=db,
            user=user,
            chat_id=chat_id,
            update=dummy_update,
        )
        return {"ok": True, "automation_history": True}

    wf_id = data.split(":", 1)[1].strip()
    target_wf = None
    try:
        avail = list_available_workflows(current_user=user, db=db)
        for item in avail.get("items", []):
            if str(item.get("id") or "") == wf_id:
                target_wf = item
                break
    except Exception:
        target_wf = None

    if target_wf is None:
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Workflow tidak ditemukan.", "show_alert": True},
        )
        return {"ok": True, "automation_not_found": True}

    schema = target_wf.get("input_schema") or {}
    properties = schema.get("properties") or {}
    required = list(schema.get("required") or [])

    if required:
        sample_payload = {k: f"isi_{k}" for k in properties.keys()}
        sample_json = json.dumps(sample_payload, ensure_ascii=False)
        await _send_raw_telegram_method(
            "answerCallbackQuery",
            {"callback_query_id": cb_id, "text": "Butuh parameter input."},
        )
        msg_text = (
            "\u2699\ufe0f " + str(target_wf.get("name", "Workflow"))
            + " membutuhkan parameter wajib: " + ", ".join(required)
            + "\n\nSalin dan sesuaikan nilai perintah berikut lalu kirim:\n"
            + f"/automation_run {wf_id} {sample_json}"
        )
        await _send_message(chat_id, msg_text, reply_markup=_workspace_menu_markup())
        return {"ok": True, "automation_prompt_params": True}

    await _send_raw_telegram_method(
        "answerCallbackQuery",
        {"callback_query_id": cb_id, "text": "Menjalankan workflow..."},
    )
    await _handle_automation_command(
        text=f"/automation_run {wf_id}",
        command="/automation_run",
        db=db,
        user=user,
        chat_id=chat_id,
        update=dummy_update,
    )
    return {"ok": True, "automation_executed": True}


async def _handle_automation_command(
    text: str,
    command: str,
    db: Session,
    user: User,
    chat_id: int,
    update: TelegramCentralUpdate,
):
    """Menjalankan fitur Automation melalui fungsi backend yang sama dengan web."""

    update.user_id = user.id
    db.commit()

    response = ""
    ambiguous_result = False

    try:
        if command in {"/automation", "⚙️ automation"}:
            result = list_available_workflows(current_user=user, db=db)
            items = result.get("items", [])

            if not items:
                response = (
                    "⚙️ AUTOMATION\n\n"
                    "Belum ada workflow yang tersedia untuk akun Anda.\n"
                    "Jika seharusnya Anda memiliki akses, hubungi Admin "
                    "untuk memeriksa izin workflow."
                )
            else:
                lines = [
                    "⚙️ DAFTAR AUTOMATION",
                    "",
                    f"Workflow yang dapat Anda gunakan: {len(items)}",
                    "",
                ]

                for index, item in enumerate(items, start=1):
                    lines.extend([
                        f"{index}. {item.get('name', 'Workflow')}",
                        f"   ID: {item['id']}",
                    ])

                    description = str(item.get("description") or "").strip()
                    if description:
                        lines.append("   " + description[:250])

                    schema = item.get("input_schema") or {}
                    properties = schema.get("properties") or {}
                    required = set(schema.get("required") or [])

                    if properties:
                        fields = []
                        for key, definition in list(properties.items())[:15]:
                            suffix = " (wajib)" if key in required else ""
                            fields.append(f"{key}{suffix}")
                        lines.append("   Input: " + ", ".join(fields))
                    else:
                        lines.append("   Input: tidak ada parameter")

                    lines.append("")

                lines.extend([
                    "▶️ MENJALANKAN WORKFLOW",
                    "Gunakan format:",
                    "/automation_run ID_WORKFLOW",
                    "",
                    "Jika membutuhkan input:",
                    '/automation_run ID_WORKFLOW {"parameter":"nilai"}',
                    "",
                    "Atau klik tombol workflow di bawah ini untuk menjalankan langsung:",
                ])
                response = "\n".join(lines)
                auto_markup = _automation_inline_markup(items)
                if auto_markup:
                    sent = await _send_raw_telegram_method(
                        "sendMessage",
                        {"chat_id": chat_id, "text": response[:4000], "reply_markup": auto_markup},
                    )
                    update.status = "completed" if sent else "failed"
                    update.error_code = None if sent else "telegram_automation_delivery_failed"
                    update.completed_at = _utc_now()
                    db.commit()
                    return {"ok": True, "automation": True, "reply_sent": bool(sent), "status": update.status}

        elif command == "/automation_history":
            result = list_my_executions(
                limit=10,
                current_user=user,
                db=db,
            )
            items = result.get("items", [])

            if not items:
                response = (
                    "🧾 RIWAYAT AUTOMATION\n\n"
                    "Belum ada riwayat eksekusi."
                )
            else:
                status_labels = {
                    "completed": "✅ Berhasil",
                    "failed": "❌ Gagal",
                    "running": "⏳ Berjalan",
                    "unknown": "⚠️ Perlu diperiksa",
                }
                lines = ["🧾 RIWAYAT AUTOMATION", ""]

                for index, item in enumerate(items, start=1):
                    created = item.get("created_at")
                    date_text = (
                        created.strftime("%d-%m-%Y %H:%M")
                        if created else "-"
                    )
                    status = str(item.get("status") or "unknown")
                    lines.extend([
                        f"{index}. {item.get('workflow_name') or 'Workflow'}",
                        f"   Status: {status_labels.get(status, status)}",
                        f"   Waktu: {date_text}",
                        f"   ID eksekusi: {item.get('id')}",
                    ])

                    if item.get("duration_ms") is not None:
                        lines.append(
                            f"   Durasi: {item['duration_ms']} ms"
                        )

                    if item.get("result_data") is not None:
                        preview = json.dumps(
                            item["result_data"],
                            ensure_ascii=False,
                            default=str,
                        )
                        lines.append("   Hasil: " + preview[:900])

                    if item.get("error_message"):
                        lines.append(
                            "   Keterangan: "
                            + str(item["error_message"])[:300]
                        )

                    lines.append("")

                response = "\n".join(lines)

        elif command == "/automation_run":
            parts = text.strip().split(maxsplit=2)

            if len(parts) < 2:
                response = (
                    "Format perintah belum lengkap.\n\n"
                    "/automation_run ID_WORKFLOW\n"
                    '/automation_run ID_WORKFLOW {"parameter":"nilai"}\n\n'
                    "Gunakan /automation untuk melihat daftar workflow."
                )
            else:
                workflow_id = parts[1]
                input_data = {}

                if len(parts) == 3:
                    try:
                        input_data = json.loads(parts[2])
                    except (ValueError, TypeError):
                        response = (
                            "Input JSON tidak valid. Periksa tanda kutip, "
                            "kurung kurawal, dan format JSON."
                        )

                    if not response and not isinstance(input_data, dict):
                        response = (
                            "Input harus berupa objek JSON, "
                            'contoh: {"nama":"Budi"}.'
                        )

                if not response:
                    result = await execute_automation_workflow(
                        workflow_id,
                        AutomationExecuteRequest(input_data=input_data),
                        current_user=user,
                        db=db,
                    )

                    execution = result.get("execution") or {}
                    status = execution.get("status", "unknown")
                    execution_id = execution.get("id", "-")

                    if status == "completed" and result.get("success"):
                        response = (
                            "✅ AUTOMATION BERHASIL\n\n"
                            f"Workflow: {execution.get('workflow_name', '-')}"
                            f"\nID eksekusi: {execution_id}"
                        )
                    elif status == "unknown":
                        ambiguous_result = True
                        response = (
                            "⚠️ STATUS EKSEKUSI BELUM DAPAT DIPASTIKAN\n\n"
                            f"Workflow: {execution.get('workflow_name', '-')}"
                            f"\nID eksekusi: {execution_id}\n\n"
                            "Periksa /automation_history atau hubungi Admin. "
                            "Jangan jalankan ulang sebelum statusnya dipastikan."
                        )
                    else:
                        response = (
                            "❌ AUTOMATION GAGAL\n\n"
                            f"Workflow: {execution.get('workflow_name', '-')}"
                            f"\nID eksekusi: {execution_id}"
                            f"\nKeterangan: {result.get('message') or 'Eksekusi gagal.'}"
                        )

                    if execution.get("result_data") is not None:
                        preview = json.dumps(
                            execution["result_data"],
                            ensure_ascii=False,
                            default=str,
                        )
                        response += "\n\nHasil:\n" + preview[:1800]

        else:
            response = (
                "Perintah Automation tidak dikenal. "
                "Gunakan /automation, /automation_run, "
                "atau /automation_history."
            )

    except HTTPException as exc:
        response = "Permintaan Automation ditolak: " + str(exc.detail)[:500]
        # Kesalahan server saat menyimpan status eksekusi bisa terjadi
        # setelah webhook n8n telah dipanggil. Jangan menganggap aman diulang.
        if exc.status_code >= 500:
            ambiguous_result = True

    except Exception:
        response = (
            "Automation mengalami kendala internal. "
            "Periksa /automation_history sebelum mencoba kembali."
        )
        ambiguous_result = True

    delivered = await _send_message(
        chat_id,
        response,
        reply_markup=_workspace_menu_markup(),
    )

    if delivered is True:
        update.status = "completed"
        update.error_code = None
    elif delivered is False:
        update.status = "needs_review" if ambiguous_result else "failed"
        update.error_code = "telegram_automation_delivery_failed"
    else:
        update.status = "needs_review"
        update.error_code = "telegram_automation_delivery_uncertain"

    # needs_review berarti hasil operasi/pengiriman belum pasti.
    # Jangan menandainya sebagai selesai agar tidak memberi kesan
    # bahwa pengiriman Telegram telah terkonfirmasi.
    update.completed_at = (
        None if update.status == "needs_review" else _utc_now()
    )
    db.commit()

    return {
        "ok": True,
        "automation": True,
        "reply_sent": delivered,
        "status": update.status,
    }


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

            update.document_title = resolve_document_title(update.document_title, update.document_content, conversation_id=getattr(update, "conversation_id", None) or getattr(assistant_message, "conversation_id", None), db=db, pure_export_checker=_is_pure_export_command, conv_model=globals().get("Conversation"))

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
    """Tangani File Saya dengan pencatatan hasil pengiriman Telegram."""
    raw = command_text.strip()
    parts = raw.split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower() if parts else "" if parts else "" if parts else ""
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
    operation_error = None
    file_delivery = None

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
                    file_delivery = await send_user_image_preview(
                        chat_id, user.id, argument
                    )
                    if file_delivery is True:
                        reply = f"Preview gambar untuk file ID {argument} berhasil dikirim."
                    elif file_delivery is False:
                        operation_error = "telegram_file_delivery_failed"
                        reply = "Telegram menolak pengiriman preview gambar. Silakan periksa file dan coba kembali."
                    else:
                        update.status = "needs_review"
                        update.error_code = "telegram_file_delivery_uncertain"
                        update.completed_at = None
                        db.commit()
                        return True
                else:
                    reply = preview_user_file(user.id, argument)

        elif command == "/unduh":
            if not argument:
                reply = "Format: /unduh ID_FILE"
            else:
                file_delivery = await send_user_file(
                    chat_id, user.id, argument
                )
                if file_delivery is True:
                    reply = f"File dengan ID {argument} berhasil dikirim."
                elif file_delivery is False:
                    operation_error = "telegram_file_delivery_failed"
                    reply = "Telegram menolak pengiriman file. Silakan periksa file dan coba kembali."
                else:
                    update.status = "needs_review"
                    update.error_code = "telegram_file_delivery_uncertain"
                    update.completed_at = None
                    db.commit()
                    return True

        elif command == "/rename_file":
            if "|" not in argument:
                reply = "Format: /rename_file ID_FILE|nama_baru.ext"
            else:
                file_id, new_name = (
                    part.strip() for part in argument.split("|", 1)
                )
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
        operation_error = "workspace_operation_failed"
    except (ValueError, OSError):
        reply = "Operasi File Saya gagal. Periksa kembali ID, nama, atau lokasi file."
        operation_error = "workspace_operation_failed"

    # Jangan menganggap pesan balasan berhasil sebelum Telegram mengonfirmasi.
    delivered = await _send_message(
        chat_id,
        reply,
        reply_markup=_workspace_menu_markup(),
    ) if reply else True

    if delivered is None:
        update.status = "needs_review"
        update.error_code = (
            "telegram_file_confirmation_uncertain"
            if file_delivery is True
            else "telegram_file_reply_uncertain"
        )
        update.completed_at = None

    elif delivered is False:
        update.status = "failed"
        update.error_code = (
            "telegram_file_confirmation_failed"
            if file_delivery is True
            else "telegram_file_reply_failed"
        )
        update.completed_at = _utc_now()

    elif operation_error:
        update.status = "failed"
        update.error_code = operation_error
        update.completed_at = _utc_now()

    elif file_delivery is False:
        update.status = "failed"
        update.error_code = "telegram_file_delivery_failed"
        update.completed_at = _utc_now()

    else:
        update.status = "completed"
        update.error_code = None
        update.completed_at = _utc_now()

    db.commit()
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

    callback_query = payload.get("callback_query")
    if isinstance(callback_query, dict):
        cb_res = await _handle_filesaya_callback(callback_query, db)
        if cb_res is not None:
            return cb_res
        auto_cb_res = await _handle_automation_callback(callback_query, db)
        if auto_cb_res is not None:
            return auto_cb_res

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
    command = parts[0].split("@", 1)[0].lower() if parts else ""

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
            caption_cmd = str(message.get("caption") or "").strip()
            if caption_cmd and not caption_cmd.startswith("/"):
                await _send_message(chat_id, reply)
                text = caption_cmd
                command = ""
            else:
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

    media_keys = ( "video", "animation", "audio", "voice",
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

    if (
        command in {"/automation", "/automation_run", "/automation_history"}
        or menu_choice == "⚙️ Automation"
    ):
        automation_command = (
            command
            if command in {
                "/automation",
                "/automation_run",
                "/automation_history",
            }
            else "⚙️ automation"
        )
        return await _handle_automation_command(
            text=text,
            command=automation_command,
            db=db,
            user=user,
            chat_id=chat_id,
            update=update,
        )

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

    if menu_choice == "📚 Riwayat Chat":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()

        conversations = (
            db.query(Conversation)
            .filter(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc())
            .limit(10)
            .all()
        )

        if not conversations:
            history_text = (
                "📚 RIWAYAT CHAT\n\n"
                "Belum ada percakapan tersimpan."
            )
        else:
            lines = [
                "📚 RIWAYAT CHAT",
                "",
                "10 percakapan terakhir:",
                "",
            ]

            for index, conv in enumerate(conversations, start=1):
                title = (conv.title or "Percakapan").strip()
                updated = (
                    conv.updated_at.strftime("%d-%m-%Y %H:%M")
                    if conv.updated_at else "-"
                )
                lines.append(f"{index}. {title[:150]}")
                lines.append(f"   🕒 {updated}")
                lines.append("")

            history_text = "\n".join(lines)

        await _send_message(
            chat_id,
            history_text,
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "history_list": True}

    if menu_choice == "🪙 Token & Top Up":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()

        lines = [
            "🪙 TOKEN & TOP UP ONE MASTER AI",
            "",
        ]

        if user.role == "admin":
            lines.extend([
                "👑 STATUS AKUN ADMIN",
                "Kuota AI: Tanpa batas",
                "",
                "Saldo Top Up: Tidak berlaku untuk akun Admin.",
                "Pemakaian harian: Tidak dibatasi.",
                "Sisa kuota harian: Tidak terbatas.",
                "",
                "Pengelolaan saldo dan Top Up pengguna tersedia "
                "melalui Panel Admin web app.",
            ])
        else:
            # Menggunakan perhitungan yang sama dengan GET /api/v1/quota.
            daily_limit, daily_used = daily_token_usage(db, user)
            daily_remaining = (
                max(0, daily_limit - daily_used)
                if daily_limit is not None else None
            )

            wallet = (
                db.query(TokenWallet)
                .filter(TokenWallet.user_id == user.id)
                .first()
            )
            balance = int(wallet.topup_balance or 0) if wallet else 0

            used_percent = (
                min(100, (daily_used / daily_limit) * 100)
                if daily_limit and daily_limit > 0 else 0
            )

            lines.extend([
                "📊 KUOTA AI HARI INI",
                f"Pemakaian hari ini: {daily_used:,}".replace(",", ".")
                + " token",
                "Batas harian: "
                + (
                    f"{daily_limit:,}".replace(",", ".") + " token"
                    if daily_limit is not None else "Tanpa batas"
                ),
                "Persentase pemakaian: "
                + f"{used_percent:.1f}%".replace(".", ","),
                "Sisa batas harian: "
                + (
                    f"{daily_remaining:,}".replace(",", ".") + " token"
                    if daily_remaining is not None else "Tidak terbatas"
                ),
                "",
            ])

            if balance < 0:
                lines.extend([
                    "🔴 UTANG TOP UP",
                    f"{abs(balance):,}".replace(",", ".")
                    + " token (belum lunas)",
                    "Akses AI diblokir sampai utang dilunasi "
                    "melalui Top Up atau kredit Admin.",
                ])
            else:
                lines.extend([
                    "💰 SALDO TOP UP",
                    f"{balance:,}".replace(",", ".") + " token",
                ])

            lines.extend(["", "📦 PAKET TOP UP AKTIF"])

            plans = (
                db.query(SubscriptionPlan)
                .filter(SubscriptionPlan.is_active.is_(True))
                .order_by(
                    SubscriptionPlan.sort_order.asc(),
                    SubscriptionPlan.name.asc(),
                )
                .all()
            )

            if not plans:
                lines.append("Belum ada paket Top Up yang aktif.")
            else:
                now_wit = wit_now()

                for index, plan in enumerate(plans, start=1):
                    active_users = (
                        db.query(UserSubscription.user_id)
                        .filter(
                            UserSubscription.plan_id == plan.id,
                            UserSubscription.status == "active",
                            UserSubscription.current_period_start <= now_wit,
                            UserSubscription.current_period_end > now_wit,
                        )
                        .distinct()
                        .count()
                    )

                    max_users = (
                        int(plan.max_users)
                        if plan.max_users is not None else None
                    )
                    is_full = (
                        max_users is not None
                        and active_users >= max_users
                    )

                    price = f"Rp{int(plan.price_idr):,}".replace(",", ".")
                    token_amount = (
                        f"{int(plan.token_amount):,}".replace(",", ".")
                    )
                    daily_limit_text = (
                        f"{int(plan.daily_token_limit):,}".replace(",", ".")
                    )

                    lines.extend([
                        "",
                        f"{index}. {plan.name}",
                        f"   Harga: {price}",
                        f"   Token: {token_amount} token",
                        f"   Batas harian: {daily_limit_text} token",
                        f"   Masa aktif: {int(plan.duration_days)} hari",
                    ])

                    if max_users is None:
                        lines.append(
                            f"   Pengguna aktif: {active_users} "
                            "· Tanpa batas kapasitas"
                        )
                    else:
                        available_slots = max(
                            0, max_users - active_users
                        )
                        lines.append(
                            f"   Pengguna aktif: {active_users}/{max_users}"
                            f" · Sisa slot: {available_slots}"
                        )

                    if is_full:
                        lines.append("   🔴 Kapasitas penuh")
                    else:
                        lines.append("   🟢 Kapasitas tersedia")

                    if plan.qris_image:
                        lines.append("   QRIS: Tersedia")
                    else:
                        lines.append("   ⚠️ QRIS belum tersedia")

            # Riwayat hanya milik pengguna Telegram yang sedang terhubung.
            lines.extend(["", "🧾 RIWAYAT TOP UP TERAKHIR"])

            requests = (
                db.query(TopUpRequest)
                .filter(TopUpRequest.user_id == user.id)
                .order_by(TopUpRequest.created_at.desc())
                .limit(10)
                .all()
            )

            if not requests:
                lines.append("Belum ada pengajuan Top Up.")
            else:
                status_labels = {
                    "pending": "⏳ Menunggu verifikasi",
                    "approved": "✅ Disetujui",
                    "rejected": "❌ Ditolak",
                }

                def format_wit_date(value):
                    if value is None:
                        return "-"
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return value.astimezone(
                        WIT
                    ).strftime("%d-%m-%Y %H:%M WIT")

                for index, item in enumerate(requests, start=1):
                    amount = (
                        f"Rp{int(item.amount_idr):,}".replace(",", ".")
                    )
                    tokens = (
                        f"{int(item.token_amount):,}".replace(",", ".")
                    )
                    status = status_labels.get(
                        item.status, str(item.status)
                    )

                    lines.extend([
                        "",
                        f"{index}. {amount}",
                        f"   Token: {tokens}",
                        f"   Status: {status}",
                        "   Diajukan: "
                        + format_wit_date(item.created_at),
                    ])

                    if item.reviewed_at:
                        lines.append(
                            "   Diperiksa: "
                            + format_wit_date(item.reviewed_at)
                        )

                    if item.admin_note:
                        lines.append(
                            "   Catatan Admin: "
                            + item.admin_note[:300]
                        )

            lines.extend([
                "",
                "💳 Untuk mengajukan Top Up dan mengunggah "
                "bukti pembayaran, gunakan halaman Top Up web app:",
                "https://gamalama-mht.my.id/topup",
                "",
                "Bot Top Up resmi: @OmAIpay_Bot",
                "",
                "Token baru masuk setelah pembayaran disetujui Admin.",
            ])

        await _send_message(
            chat_id,
            "\n".join(lines),
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "workspace_section": "token_topup"}

    if menu_choice == "👤 Profil & Bantuan":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()

        profile = build_user_info(db, user)

        status_labels = {
            "active": "Aktif",
            "pending": "Menunggu persetujuan Admin",
            "rejected": "Ditolak",
        }
        role_labels = {
            "admin": "Administrator",
            "user": "Pengguna",
        }

        joined_at = (
            user.created_at.strftime("%d-%m-%Y")
            if user.created_at else "-"
        )

        lines = [
            "👤 PROFIL AKUN ONE MASTER AI",
            "",
            f"Nama: {profile.full_name or '-'}",
            f"Email: {profile.email or '-'}",
            f"Profesi: {profile.profession or '-'}",
            f"Organisasi: {profile.organization or '-'}",
            f"Status akun: {status_labels.get(profile.status, profile.status)}",
            f"Peran: {role_labels.get(profile.role, profile.role)}",
            f"Bergabung sejak: {joined_at}",
            "",
            "📦 INFORMASI PAKET",
            f"Paket: {profile.package_name}",
        ]

        if profile.package_is_default:
            lines.append("Jenis paket: Paket dasar (default)")
        else:
            lines.append("Jenis paket: Langganan aktif")

        if profile.package_expires_at:
            lines.append(
                "Masa berlaku sampai: "
                + profile.package_expires_at.replace("T", " ")
            )

        if profile.package_days_remaining is not None:
            lines.append(
                f"Sisa masa berlaku: {profile.package_days_remaining} hari"
            )

        if link.telegram_username:
            lines.extend([
                "",
                "Telegram: @" + link.telegram_username.lstrip("@"),
            ])

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🆘 PUSAT BANTUAN OM.AI",
            "",
            "💬 CHAT AI",
            "Pilih Chat AI, lalu kirim pertanyaan atau instruksi "
            "seperti percakapan biasa.",
            "",
            "📚 RIWAYAT CHAT",
            "Gunakan menu Riwayat Chat untuk melihat percakapan "
            "yang pernah dilakukan.",
            "",
            "📁 FILE SAYA",
            "Gunakan menu File Saya untuk mengelola berkas pribadi "
            "Anda sesuai fitur yang tersedia.",
            "",
            "📄 BUAT DOKUMEN",
            "Kirim instruksi pembuatan dokumen, misalnya laporan "
            "dalam Word, proposal dalam PDF, tabel dalam Excel, "
            "atau presentasi PowerPoint.",
            "",
            "🪙 TOKEN & TOP UP",
            "Periksa saldo Top Up, batas harian, pemakaian, dan "
            "paket melalui menu Token & Top Up.",
            "Pengajuan Top Up dilakukan melalui:",
            "https://gamalama-mht.my.id/topup",
            "Bot pembayaran resmi: @OmAIpay_Bot",
            "",
            "🔐 KEAMANAN AKUN",
            "Jangan membagikan kata sandi, kode pengaitan akun, "
            "atau informasi akses kepada orang lain.",
            "Data profil dan file yang ditampilkan di bot ini "
            "mengikuti akun OM.AI yang tertaut.",
            "",
            "Jika mengalami kendala, simpan pesan kesalahan "
            "dan sampaikan kepada Admin melalui kanal resmi OM.AI.",
        ])

        await _send_message(
            chat_id,
            "\n".join(lines),
            reply_markup=_workspace_menu_markup(),
        )
        return {"ok": True, "workspace_section": "profile_help"}

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
    }

    if menu_choice == "📁 File Saya":
        update.user_id = user.id
        update.status = "completed"
        update.completed_at = _utc_now()
        db.commit()
        inline_del = _filesaya_delete_inline_markup(user.id)
        if inline_del:
            await _send_raw_telegram_method(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": list_user_files(user.id),
                    "reply_markup": inline_del,
                },
            )
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

    # Jalur Direct Export: jika user hanya meminta ekspor jawaban terakhir ke file
    # (tanpa instruksi ubah isi), render langsung tanpa memanggil LLM (0 token, instan).
    if (
        document_request is not None
        and _is_pure_export_command(text.strip())
        and link.conversation_id
    ):
        recent_assistant_msgs = (
            db.query(Message)
            .filter(
                Message.conversation_id == link.conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.created_at.desc())
            .limit(10)
            .all()
        )
        msg_ids = [m.id for m in recent_assistant_msgs]
        doc_by_msg_id = {}
        if msg_ids:
            update_cls = type(update)
            doc_updates = (
                db.query(update_cls)
                .filter(
                    update_cls.assistant_message_id.in_(msg_ids),
                    update_cls.document_content.isnot(None),
                )
                .order_by(update_cls.created_at.desc())
                .all()
            )
            for u_doc in doc_updates:
                if (u_doc.document_content or "").strip() and u_doc.assistant_message_id not in doc_by_msg_id:
                    doc_by_msg_id[u_doc.assistant_message_id] = u_doc

        source_body = None
        source_title = None
        for m in recent_assistant_msgs:
            u_doc = doc_by_msg_id.get(m.id)
            if u_doc is not None:
                source_body = u_doc.document_content.strip()
                source_title = (u_doc.document_title or "").strip() or None
                break
            content_clean = (m.content or "").strip()
            if len(content_clean) >= 300:
                source_body = content_clean
                break

        if source_body is not None:
            try:
                update.document_content = source_body
                base_title = source_title or update.document_title
                update.document_title = resolve_document_title(base_title, source_body, conversation_id=link.conversation_id, db=db, pure_export_checker=_is_pure_export_command, conv_model=globals().get("Conversation"))
                create_user_document(
                    db=db,
                    user=user,
                    title=update.document_title,
                    body_text=source_body,
                    file_format=update.document_format,
                    file_id=update.generated_file_id,
                )
                update.document_delivery_status = "sending"
                db.commit()

                document_sent = await send_generated_document(
                    chat_id=chat_id,
                    user_id=user.id,
                    file_id=update.generated_file_id,
                    file_format=update.document_format,
                    title=update.document_title,
                )
                if document_sent:
                    update.document_delivery_status = "sent"
                    update.response_sent_at = _utc_now()
                    update.status = "completed"
                    update.error_code = None
                    update.completed_at = _utc_now()
                    db.commit()
                    return {"ok": True, "processed": True, "mode": "direct_export"}

                print("[telegram] Direct Export gagal kirim dokumen, fallback ke _chat_impl.", flush=True)
                update.document_delivery_status = "pending"
                db.commit()
            except Exception as exc:
                print(f"[telegram] Direct Export error: {type(exc).__name__}: {exc}", flush=True)
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
                file_id=_automatic_upload_context_file_id(user.id, text.strip()),
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
