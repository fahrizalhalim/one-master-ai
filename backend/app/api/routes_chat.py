"""Chat dengan penyimpanan riwayat percakapan per user."""

import logging
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
HISTORY_LIMIT = 10

@router.get("/models")
def available_models(current_user: User = Depends(get_current_user)):
    """Daftar model yang sudah dikonfigurasi di server."""
    models = []
    if GROQ_API_KEY:
        models.append({
            "id": "groq-gpt-oss-120b",
            "label": "Groq · GPT-OSS 120B (utama)",
        })
    models.append({
        "id": "default",
        "label": "Ai.sumopod (manual/cadangan)",
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
    topup_balance = max(0, int(wallet.topup_balance)) if wallet else 0

    return {
        "unlimited": False,
        "daily_limit": daily_limit,
        "daily_used": daily_used,
        "daily_remaining": daily_remaining,
        "topup_balance": topup_balance,
        "total_balance": topup_balance,
        "timezone": "Asia/Jayapura",
    }


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    model_id: Literal["default", "groq-gpt-oss-120b"] = "groq-gpt-oss-120b"
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


def _get_own_conversation(
    db: Session, conversation_id: str, user: User
) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")
    return conv


def _save_message(db: Session, conversation_id: str, role: str, content: str, skill: str = ""):
    db.add(
        Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            skill=skill,
        )
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
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

    # Konteks: N pesan terakhir percakapan ini
    history = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    history = list(reversed(history))

    history_text = "\n".join(
        f"{'User' if m.role == 'user' else 'Asisten'}: {m.content[:500]}"
        for m in history
    )

    history_block = ""
    if history_text:
        history_block = f"Riwayat percakapan (terbaru di akhir):\n{history_text}\n"

    # Konteks file terunggah (maks 6000 char agar prompt tidak membengkak)
    file_block = ""
    if data.file_id:
        from app.api.routes_files import _user_dir

        extract = _user_dir(current_user.id) / f"{data.file_id}.extract.txt"
        if extract.exists():
            text = extract.read_text(encoding="utf-8", errors="ignore")
            file_block = (
                "Pengguna melampirkan dokumen berikut. Gunakan isinya sebagai "
                "konteks utama jawaban jika relevan:\n\n--- AWAL DOKUMEN ---\n"
                f"{text[:6000]}\n--- AKHIR DOKUMEN ---\n"
            )
        else:
            raise HTTPException(status_code=404, detail="Lampiran tidak ditemukan.")

    # Siapkan prompt lengkap sebelum reservasi.
    prepared = prepare_master_prompt(
        user_message=data.message,
        profession=current_user.profession or "umum",
        full_name=current_user.full_name or "",
        history_text=history_text,
        file_text=file_block,
    )

    # Estimasi reservasi awal: token prompt + cadangan output 512 token.
    # Batas output provider tetap 4096 token.
    estimated_tokens = max(1, (len(prepared["prompt"]) + 2) // 3) + 512

    # Reservasi saldo paket/top-up dan batas harian sebelum memanggil AI.
    reservation = reserve_tokens(
        db=db,
        user=current_user,
        estimated_tokens=estimated_tokens,
        reference=f"chat:{conv.id}",
        description="Chat aplikasi ONE MASTER AI",
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

    return ChatResponse(
        conversation_id=conv.id,
        skill=result["skill"],
        answer=result["answer"],
    )
