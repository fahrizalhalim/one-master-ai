"""Chat dengan penyimpanan riwayat percakapan per user."""

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from app.core.config import GROQ_API_KEY
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.user import User
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.subscription import UserSubscription, TokenWallet
from sqlalchemy import func
from app.services.master_agent import master_agent, prepare_master_prompt, ProviderCallError
from app.services.context_retrieval import search_past_conversations, search_past_files
from app.services.quota_service import daily_token_usage
from app.services.token_service import (
    WIT,
    reserve_tokens,
    cancel_reservation,
    complete_reservation,
)


router = APIRouter(prefix="/api/v1", tags=["chat"])
logger = logging.getLogger(__name__)

# Jumlah pesan terakhir yang dikirim sebagai konteks ke AI
HISTORY_LIMIT = 30

@router.get("/models")
def available_models(current_user: User = Depends(get_current_user)):
    """Daftar model yang sudah dikonfigurasi di server."""
    models = [{
        "id": "default",
        "label": "OM.AI Pro",
    }]
    if GROQ_API_KEY:
        models.append({
            "id": "groq-gpt-oss-120b",
            "label": "Groq · GPT-OSS 120B (Free)",
        })
    return {"models": models}



@router.get("/quota")
def my_quota(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Informasi kuota harian WIT dan saldo Top Up."""

    if current_user.role == "admin":
        return {
            "unlimited": True,
            "daily_limit": None,
            "daily_used": 0,
            "daily_remaining": None,
            "topup_balance": None,
            "total_balance": None,
            "timezone": "Asia/Jayapura",
        }

    daily_limit, daily_used = daily_token_usage(db, current_user)
    daily_remaining = max(0, daily_limit - daily_used)

    wallet = (
        db.query(TokenWallet)
        .filter(TokenWallet.user_id == current_user.id)
        .first()
    )
    topup_balance = int(wallet.topup_balance or 0) if wallet else 0

    return {
        "unlimited": False,
        "daily_limit": daily_limit,
        "daily_used": daily_used,
        "daily_remaining": daily_remaining,
        "topup_balance": topup_balance,
        "total_balance": topup_balance,
        "topup_debt": max(0, -topup_balance),
        "timezone": "Asia/Jayapura",
    }


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    model_id: Literal["default", "groq-gpt-oss-120b"] = "default"
    conversation_id: str | None = Field(
        default=None, description="ID percakapan (kosong = mulai baru)"
    )
    file_id: str | None = Field(
        default=None, max_length=24, description="ID file terunggah sebagai konteks"
    )


class ChatResponse(BaseModel):
    conversation_id: str
    skill: str
    answer: str
    assistant_message_id: str
    reservation_id: str | None = None
    document_content: str | None = None
    actions: list[dict] | None = None


def _get_own_conversation(
    db: Session, conversation_id: str, user: User
) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")
    return conv


def _save_message(db: Session, conversation_id: str, role: str, content: str, skill: str = ""):
    conversation = db.get(Conversation, conversation_id)
    if conversation is not None:
        conversation.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(
        Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            skill=skill,
        )
    )




def _select_relevant_history(history: list, user_message: str) -> str:
    """Pilih riwayat berdasarkan jawaban singkat, topik, atau lanjutan."""

    if not history:
        return ""

    def normalize(value):
        value = (value or "").lower().strip()
        value = re.sub(r"\bpolibag\b|\bpoli\s*bag\b", "polybag", value)
        return value

    stopwords = {
        "yang", "dan", "atau", "untuk", "dari", "dengan",
        "pada", "adalah", "ini", "itu", "saya", "aku",
        "anda", "kamu", "tolong", "mohon", "bisa", "buat",
        "bagaimana", "apakah", "jelaskan", "berikan",
        "sebuah", "dalam", "agar", "juga", "lebih",
        "sekarang", "maka", "akan", "dapat", "sudah",
        "belum", "tentang", "pembahasan",
    }

    def words(value):
        return {
            word for word in re.findall(r"[a-z0-9_]{3,}", normalize(value))
            if word not in stopwords
        }

    current = normalize(user_message)
    current_words = words(current)

    # Kelompokkan riwayat menjadi satu rangkaian user dan jawaban asisten.
    turns = []
    i = 0

    while i < len(history):
        if history[i].role != "user":
            i += 1
            continue

        end = i + 1
        while end < len(history) and history[end].role != "user":
            end += 1

        turns.append(history[i:end])
        i = end

    if not turns:
        return ""

    selected_turns = []

    # 1. Jawaban singkat berupa pilihan media/wadah tanam.
    media_answers = {
        "polybag", "pot", "tanah", "hidroponik",
        "lahan", "bedengan", "karung", "ember",
    }

    if current in media_answers:
        for turn in reversed(turns):
            assistant_text = " ".join(
                normalize(m.content)
                for m in turn if m.role != "user"
            )
            if any(answer in words(assistant_text) for answer in media_answers & current_words):
                selected_turns = [turn]
                break

    # 2. Jawaban ukuran: cari riwayat yang benar-benar membahas ukuran.
    numeric_measurement = bool(re.fullmatch(
        r"\s*\d+(?:[.,]\d+)?\s*(?:mm|cm|m|meter|inci|inch)\s*",
        current,
    ))

    if not selected_turns and numeric_measurement:
        size_context = re.compile(
            r"ukuran|tinggi|jarak|kedalaman|diameter|panjang|lebar|"
            r"\d+\s*[x×]\s*\d+\s*(?:mm|cm|m)|"
            r"\d+(?:[.,]\d+)?\s*(?:mm|cm|meter|inci|inch)",
            re.I,
        )

        for turn in reversed(turns):
            assistant_text = " ".join(
                normalize(m.content)
                for m in turn if m.role != "user"
            )
            if size_context.search(assistant_text):
                # Sertakan giliran sebelumnya karena jawaban ukuran
                # mungkin merujuk pada ukuran yang sudah disebutkan.
                turn_index = turns.index(turn)
                selected_turns = turns[max(0, turn_index - 1):turn_index + 1]
                break

    # 3. Jawaban angka untuk pertanyaan jumlah.
    numeric_answer = bool(re.fullmatch(r"\s*\d+\s*", current))

    if not selected_turns and numeric_answer:
        quantity_context = re.compile(
            r"berapa banyak|berapa jumlah|jumlah .*berapa|"
            r"berapa .*bibit|berapa .*tanaman|berapa .*buah",
            re.I,
        )

        for turn in reversed(turns):
            assistant_text = " ".join(
                normalize(m.content)
                for m in turn if m.role != "user"
            )
            if quantity_context.search(assistant_text):
                selected_turns = [turn]
                break

    # 4. Cari topik spesifik di seluruh riwayat sebelum fallback lanjutan.
    if not selected_turns and current_words:
        matched = []

        for turn in turns:
            turn_text = " ".join(
                normalize(m.content) for m in turn
            )
            shared = current_words & words(turn_text)

            topic_match = (
                bool(shared)
                and (
                    len(shared) >= 2
                    or any(len(word) >= 5 for word in shared)
                )
                and len(shared) / max(1, len(current_words)) >= 0.10
            )

            if topic_match:
                matched.append(turn)

        if matched:
            selected_turns = matched

    # 5. Frasa lanjutan tanpa topik spesifik menggunakan konteks terbaru.
    follow_up_phrases = (
        "lanjutkan", "teruskan", "lebih detail",
        "jelaskan lagi", "contoh lainnya",
        "buatkan versi", "revisi bagian",
        "ubah bagian", "hapus bagian",
        "yang tadi", "sebelumnya",
        "bagaimana dengan", "kalau begitu",
        "maksudnya",
    )

    is_follow_up = any(
        re.search(
            r"(?<![a-z0-9_])" + re.escape(phrase) + r"(?![a-z0-9_])",
            current,
        )
        for phrase in follow_up_phrases
    )

    if not selected_turns and is_follow_up:
        selected_turns = turns[-3:]

    # Sliding window default: selalu sertakan 2 giliran terakhir agar AI tidak kehilangan konteks.
    if not selected_turns:
        selected_turns = turns[-2:]

    selected = [
        message
        for turn in selected_turns
        for message in turn
    ]

    return "\n".join(
        f"{'User' if m.role == 'user' else 'Asisten'}: {(m.content or '')[:4000]}"
        for m in selected
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _chat_impl(data, current_user, db)


def _chat_impl(
    data: ChatRequest,
    current_user: User,
    db: Session,
    *,
    reservation_reference: str | None = None,
    document_mode: bool = False,
):
    # Pemeriksaan status akun. Kuota ditangani token_service.
    if current_user.status != "active":
        raise HTTPException(
            status_code=403,
            detail=(
                "Akun Anda belum disetujui admin. "
                "Status: menunggu persetujuan."
            ),
        )

    # Ambil atau buat percakapan
    if data.conversation_id:
        conv = _get_own_conversation(db, data.conversation_id, current_user)
    else:
        conv = Conversation(
            user_id=current_user.id,
            title=data.message[:80],  # judul = awal pesan pertama
        )
        db.add(conv)
        db.flush()

    from app.services.web_chat_commands import handle_web_chat_command, try_export_last_assistant_message
    cmd_res = handle_web_chat_command(db, current_user, data.message)
    if cmd_res is None and not data.file_id:
        cmd_res = try_export_last_assistant_message(db, current_user, conv.id, data.message)
    if cmd_res is not None:
        user_msg = Message(conversation_id=conv.id, role="user", content=data.message, skill=cmd_res["skill"])
        asst_msg = Message(conversation_id=conv.id, role="assistant", content=cmd_res["answer"], skill=cmd_res["skill"])
        conv.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(user_msg)
        db.add(asst_msg)
        db.commit()
        db.refresh(asst_msg)
        return ChatResponse(
            conversation_id=conv.id,
            skill=cmd_res["skill"],
            answer=cmd_res["answer"],
            assistant_message_id=asst_msg.id,
            actions=cmd_res.get("actions"),
        )

    # Konteks: N pesan terakhir percakapan ini
    history = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc(), Message.role.asc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    history = list(reversed(history))

    # Pilih konteks relevan dari riwayat aktif (lewati jika file langsung dilampirkan).
    history_text = "" if data.file_id else _select_relevant_history(history, data.message)

    # Retrieval otomatis percakapan terdahulu berdasarkan relevansi.
    # Pencarian tetap dibatasi pada pemilik yang sama dan mengecualikan
    # percakapan aktif agar konteks tidak terduplikasi.
    past_history_text = ""
    if not data.file_id:
        try:
            past_history_text = search_past_conversations(
                db=db,
                user_id=str(current_user.id),
                query=data.message,
                limit=10,
                max_chars=9000,
                exclude_conversation_id=str(conv.id),
            )
        except Exception:
            logger.exception("Retrieval percakapan lama gagal.")

    if past_history_text:
        history_text = "\n\n".join(
            part for part in (history_text, past_history_text) if part
        )

    # Konteks file langsung menggunakan Workspace agar Uploads dan Hasil AI
    # sama-sama dapat diakses, tetap dengan pembatasan pemilik file.
    def _compact_document_with_audit(raw_text: str) -> str:
        import re
        raw = str(raw_text or "")
        words = len(re.findall(r"\b\w+\b", raw))
        dbl_spaces = len(re.findall(r"[A-Za-z0-9.,;:] {2,}[A-Za-z0-9]", raw))
        glued = list(dict.fromkeys(
            re.findall(r"[a-z]{3,}[.;:,][A-Z][a-z]{2,}", raw)
        ))[:12]
        headings = re.findall(r"(?m)^(\d+(?:\.\d+){1,3})\s+([^\n]{3,55})", raw)
        heading_list = ", ".join(f"{n} {t.strip()}" for n, t in headings[:45])

        text = re.sub(r"\.{4,}", " ", raw)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        m = re.search(r"\n\s*LAMPIRAN\b", text, flags=re.IGNORECASE)
        if m and len(text) - m.start() > 2200:
            head = text[:m.start()]
            tail = text[m.start():]
            text = (
                f"{head}\n\n{tail[:1100]}\n"
                "[... bagian tengah kode/lampiran dipadatkan otomatis ...]\n"
                f"{tail[-800:]}"
            )

        audit = (
            "[HASIL AUDIT OTOMATIS SISTEM (PRE-CHECK)]\n"
            f"- Estimasi jumlah kata utuh: {words} kata\n"
            f"- Spasi ganda antar-kata terdeteksi: {dbl_spaces} titik\n"
            f"- Contoh tanda baca tanpa spasi: {', '.join(glued) if glued else 'tidak ditemukan'}\n"
            f"- Urutan nomor subbab terdeteksi: {heading_list or 'tidak terdeteksi'}\n\n"
        )
        return audit + text.strip()

    file_block = ""
    image_data_url = None
    if data.file_id:
        from app.services.workspace_service import read_file, read_image_data_url, WorkspaceError

        try:
            file_result = read_file(
                str(current_user.id),
                data.file_id,
                max_chars=120000,
            )
            ext = str(file_result.get("extension") or "").lower()
            if ext in {".jpg", ".jpeg", ".png"}:
                image_data_url = read_image_data_url(str(current_user.id), data.file_id)
                if not image_data_url:
                    raise WorkspaceError("File gambar tidak valid atau rusak.")
                file_block = (
                    "INSTRUKSI WAJIB ANALISIS GAMBAR: Pengguna melampirkan gambar terbaru di bawah ini. "
                    "Amati, baca teks/objek/grafik di dalamnya secara akurat, dan jelaskan HANYA isi visual nyata yang terlihat pada gambar terlampir ini. "
                    "DILARANG KERAS menebak, mengarang, atau mengambil konteks dari dokumen PDF/skripsi/chat sebelumnya.\n"
                    f"Nama file gambar: {file_result.get('filename', 'gambar.jpg')}\n"
                )
            else:
                text = _compact_document_with_audit(file_result.get("content") or "")
                file_block = (
                    "INSTRUKSI WAJIB: Pengguna melampirkan dokumen terbaru di bawah ini. "
                    "Fokus HANYA pada dokumen ini dan ABAIKAN dokumen/proposal/skripsi lain dari chat sebelumnya.\n"
                    f"Nama file: {file_result.get('filename', 'File')}\n"
                    f"Lokasi: {file_result.get('folder_path', '')}\n\n"
                    "--- AWAL DOKUMEN ---\n"
                    f"{text}\n--- AKHIR DOKUMEN ---\n"
                )
        except (WorkspaceError, OSError, ValueError):
            raise HTTPException(
                status_code=404,
                detail="Lampiran tidak ditemukan atau tidak dapat dibaca.",
            )

    # Retrieval otomatis dokumen Workspace milik pengguna.
    # Hanya dijalankan jika tidak ada file_id yang dirujuk secara langsung.
    if not data.file_id:
        try:
            past_file_text = search_past_files(
                user_id=str(current_user.id),
                query=data.message,
                limit=3,
                max_chars=4500,
            )
            if past_file_text:
                file_block = "\n\n".join(
                    part for part in (file_block, past_file_text) if part
                )
        except Exception:
            logger.exception("Retrieval dokumen lama gagal.")

    # Siapkan prompt lengkap sebelum reservasi.
    prepared = prepare_master_prompt(
        user_message=data.message,
        profession=current_user.profession or "umum",
        full_name=current_user.full_name or "",
        history_text=history_text,
        file_text=file_block,
        user_role=current_user.role or "user",
        document_mode=document_mode,
    )

    # Estimasi prompt dipisahkan dari kapasitas output provider.
    estimated_prompt_tokens = max(
        1, (len(prepared["prompt"]) + 2) // 3
    )
    requested_output_tokens = 4096
    estimated_tokens = estimated_prompt_tokens + requested_output_tokens

    # Reservasi saldo paket/top-up dan batas harian sebelum memanggil AI.
    reservation = reserve_tokens(
        db=db,
        user=current_user,
        estimated_tokens=estimated_tokens,
        estimated_prompt_tokens=estimated_prompt_tokens,
        requested_output_tokens=requested_output_tokens,
        reference=reservation_reference or f"chat:{conv.id}",
        description=(
            "Chat Telegram pusat ONE MASTER AI"
            if reservation_reference
            else "Chat aplikasi ONE MASTER AI"
        ),
    )

    # Simpan pesan user setelah reservasi berhasil.
    _save_message(db, conv.id, "user", data.message)

    try:
        result = master_agent(
            user_message=data.message,
            profession=current_user.profession or "umum",
            history_text=history_text,
            file_text=file_block,
            prepared=prepared,
            model_id=data.model_id,
            document_mode=document_mode,
            max_output_tokens=(
                reservation.max_output_tokens
                if reservation is not None
                else 4096
            ),
            image_data_url=image_data_url,
        )
    except ProviderCallError as exc:
        db.commit()

        if reservation is not None:
            if exc.safe_to_refund:
                cancel_reservation(db, reservation.id)
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Provider menolak permintaan. Reservasi token "
                        "telah dikembalikan."
                    ),
                ) from exc

            # Hasil provider tidak pasti. Tahan saldo dan minta pemeriksaan.
            try:
                locked_reservation = (
                    db.query(TokenReservation)
                    .filter(TokenReservation.id == reservation.id)
                    .with_for_update()
                    .first()
                )
                if locked_reservation is not None and locked_reservation.status == "pending":
                    locked_reservation.status = "needs_review"
                    locked_reservation.description = (
                        (locked_reservation.description or "")
                        + f" | Provider review: {exc.reason}"
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
                                user_id=current_user.id,
                                assistant_message_id=assistant_message.id,
                                response_content=exc.answer,
                                skill=prepared["skill"],
                                prompt_tokens=exc.prompt_tokens,
                                completion_tokens=exc.completion_tokens,
                                total_tokens=None,
                                status="needs_review",
                            )
                        )

                    db.commit()
                else:
                    db.rollback()
            except Exception:
                db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Status pemrosesan AI belum dapat dipastikan. "
                        "Reservasi tidak boleh direfund otomatis."
                    ),
                ) from exc

            raise HTTPException(
                status_code=503,
                detail=(
                    "Pemrosesan AI belum dapat dipastikan. Token reservasi "
                    "ditahan untuk pemeriksaan admin. Jangan kirim ulang "
                    "permintaan yang sama."
                ),
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="Layanan AI sedang tidak tersedia.",
        ) from exc

    except Exception as exc:
        # Exception yang tidak dikenal tidak dianggap aman untuk refund.
        db.commit()

        if reservation is not None:
            try:
                locked_reservation = (
                    db.query(TokenReservation)
                    .filter(TokenReservation.id == reservation.id)
                    .with_for_update()
                    .first()
                )
                if locked_reservation is not None and locked_reservation.status == "pending":
                    locked_reservation.status = "needs_review"
                    locked_reservation.description = (
                        (locked_reservation.description or "")
                        + " | Provider review: unexpected_error"
                    )[:2000]
                    db.commit()
                else:
                    db.rollback()
            except Exception:
                db.rollback()

            raise HTTPException(
                status_code=503,
                detail=(
                    "Hasil pemrosesan AI belum dapat dipastikan. "
                    "Reservasi ditahan untuk pemeriksaan admin."
                ),
            ) from exc

        raise HTTPException(
            status_code=502,
            detail="Layanan AI sedang tidak tersedia.",
        ) from exc

    # Catat provider aktual tanpa merekam prompt, jawaban, atau API key.
    logger.warning(
        "chat_provider provider=%s model=%s route=%s prompt_tokens=%s completion_tokens=%s",
        result.get("provider", "unknown"),
        result.get("model", "unknown"),
        result.get("provider_route", "unknown"),
        result.get("prompt_tokens"),
        result.get("completion_tokens"),
    )

    # Simpan pesan assistant terlebih dahulu agar memiliki ID permanen.
    assistant_message = Message(
        conversation_id=conv.id,
        role="assistant",
        content=result["answer"],
        skill=result["skill"],
    )
    db.add(assistant_message)
    db.flush()

    if reservation is not None:
        # Simpan hasil AI sebelum settlement. Keduanya di-commit bersama.
        db.add(
            TokenReservationResult(
                reservation_id=reservation.id,
                user_id=current_user.id,
                assistant_message_id=assistant_message.id,
                response_content=result["answer"],
                skill=result["skill"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"],
                total_tokens=result["total_tokens"],
                status="received",
            )
        )
        db.commit()

        # Jika settlement gagal, hasil AI tetap tersimpan dengan status received.
        try:
            complete_reservation(
                db=db,
                reservation_id=reservation.id,
                actual_tokens=result["total_tokens"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"],
                skill=result["skill"],
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Jawaban AI sudah tersimpan, tetapi settlement token "
                    "belum berhasil. Hasil menunggu proses pemulihan; "
                    "jangan mengirim ulang permintaan yang sama."
                ),
            ) from exc

        # Settlement berhasil. Tandai hasil sebagai settled.
        stored_result = db.get(TokenReservationResult, reservation.id)
        if stored_result is None:
            raise HTTPException(
                status_code=500,
                detail="Hasil AI tersimpan tetapi catatan pemulihannya tidak ditemukan.",
            )

        stored_result.status = "settled"
        stored_result.settled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

    else:
        # Admin tidak menggunakan reservasi, tetapi statistik tetap dicatat.
        db.add(
            TokenUsage(
                user_id=current_user.id,
                skill=result["skill"],
                prompt_tokens=result["prompt_tokens"],
                completion_tokens=result["completion_tokens"],
                total_tokens=result["total_tokens"],
            )
        )
        db.commit()

# --- AUTO DOC ACTIONS ---
    doc_actions: list[dict] | None = None
    msg_low = (data.message or "").strip().lower()
    m_fmt = re.match(
        r"^(?:tolong\s+|coba\s+|silakan\s+)?(?:buat(?:kan)?|jadikan|simpan|ekspor)\s+(?:file\s+|dokumen\s+)?(pdf|docx|word)(?:\s*:|\s+tentang\b|\s+mengenai\b|\s+untuk\b|\s+berisi\b)",
        msg_low,
    )
    if m_fmt and (result.get("answer") or "").strip():
        try:
            import secrets
            from app.services import telegram_workspace_service as ws_tg
            from app.services.web_chat_commands import _infer_smart_filename

            fmt_raw = m_fmt.group(1)
            fmt = "docx" if fmt_raw in ("docx", "word") else "pdf"
            title, smart_filename = _infer_smart_filename(result["answer"], fmt)
            file_id = secrets.token_hex(6)
            ws_tg.create_user_document(
                db=db,
                user=current_user,
                title=title,
                body_text=result["answer"].strip(),
                file_format=fmt,
                file_id=file_id,
            )
            doc_actions = [
                {
                    "type": "download_file",
                    "label": f"⬇️ Unduh {smart_filename}",
                    "file_id": file_id,
                    "filename": smart_filename,
                },
                {"type": "command", "label": "📁 Lihat Daftar File", "value": "/daftar_file"},
            ]
        except Exception:
            logger.exception("Auto-export file dari prompt chat gagal.")

    return ChatResponse(
        conversation_id=conv.id,
        skill=result["skill"],
        answer=result["answer"],
        assistant_message_id=assistant_message.id,
        reservation_id=reservation.id if reservation is not None else None,
        document_content=result.get("document_content"),
            actions=doc_actions,
    )
