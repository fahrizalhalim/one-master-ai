"""Chat dengan penyimpanan riwayat percakapan per user."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.user import User
from app.services.master_agent import master_agent

router = APIRouter(prefix="/api/v1", tags=["chat"])

# Jumlah pesan terakhir yang dikirim sebagai konteks ke AI
HISTORY_LIMIT = 10


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = Field(
        default=None, description="ID percakapan (kosong = mulai baru)"
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
        )
    except Exception:
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Layanan AI sedang tidak tersedia. Coba lagi nanti.",
        )

    # Simpan jawaban AI
    _save_message(db, conv.id, "assistant", result["answer"], result["skill"])
    db.commit()

    return ChatResponse(
        conversation_id=conv.id,
        skill=result["skill"],
        answer=result["answer"],
    )
