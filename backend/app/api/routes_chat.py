"""Chat dengan penyimpanan riwayat percakapan per user."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.core.config import DAILY_CHAT_LIMIT
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.token_usage import TokenUsage
from app.models.user import User
from app.services.master_agent import master_agent

router = APIRouter(prefix="/api/v1", tags=["chat"])

# Jumlah pesan terakhir yang dikirim sebagai konteks ke AI
HISTORY_LIMIT = 10


def daily_quota(db: Session, user: User) -> tuple[int, int]:
    """Kembalikan (batas, terpakai hari ini). Admin = tanpa batas (0)."""
    if user.role == "admin":
        return 0, 0
    limit = DAILY_CHAT_LIMIT
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    used = (
        db.query(func.count(TokenUsage.id))
        .filter(
            TokenUsage.user_id == user.id,
            TokenUsage.created_at >= today_start,
        )
        .scalar()
    )
    return limit, int(used or 0)


@router.get("/quota")
def my_quota(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sisa kuota chat hari ini untuk user yang login."""
    limit, used = daily_quota(db, current_user)
    if limit == 0:
        return {"limit": 0, "used": used, "remaining": None, "unlimited": True}
    return {
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "unlimited": False,
    }


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
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
    # Rate limiting: batas chat per hari (kecuali admin)
    if current_user.status != "active":
        raise HTTPException(
            status_code=403,
            detail=(
                "Akun Anda belum disetujui admin. "
                "Status: menunggu persetujuan."
            ),
        )
    limit, used = daily_quota(db, current_user)
    if limit > 0 and used >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Kuota harian habis ({used}/{limit} chat). "
                "Kuota reset otomatis besok pagi. Hubungi admin bila butuh penambahan."
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
        db.commit()
        db.refresh(conv)

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

    # Simpan pesan user
    _save_message(db, conv.id, "user", data.message)

    # Panggil Master Agent (profesi dari profil user).
    # Jika AI gagal dijawab (mis. provider down), pesan user & percakapan
    # tetap tersimpan, dan kita kembalikan pesan error yang ramah.
    try:
        result = master_agent(
            user_message=data.message,
            profession=current_user.profession,
            history_text=history_text,
            file_text=file_block,
        )
    except Exception:
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Layanan AI sedang tidak tersedia. Coba lagi nanti.",
        )

    # Simpan jawaban AI
    _save_message(db, conv.id, "assistant", result["answer"], result["skill"])

    # Catat pemakaian token user
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
