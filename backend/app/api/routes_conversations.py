"""Endpoint riwayat percakapan: daftar, detail, hapus."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.models.conversation import Conversation, Message
from app.models.database import get_db
from app.models.user import User

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


class ConversationItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class MessageItem(BaseModel):
    role: str
    skill: str
    content: str
    created_at: str

    class Config:
        from_attributes = True


class ConversationDetail(ConversationItem):
    messages: list[MessageItem] = []


@router.get("", response_model=list[ConversationItem])
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    convs = (
        db.query(Conversation)
        .filter(Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
        .all()
    )
    return [
        ConversationItem(
            id=c.id,
            title=c.title,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )
        for c in convs
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")

    msgs = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=[
            MessageItem(
                role=m.role,
                skill=m.skill,
                content=m.content,
                created_at=m.created_at.isoformat(),
            )
            for m in msgs
        ],
    )


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Percakapan tidak ditemukan.")

    db.query(Message).filter(
        Message.conversation_id == conv.id
    ).delete()
    db.delete(conv)
    db.commit()
    return {"detail": "Percakapan dihapus."}
