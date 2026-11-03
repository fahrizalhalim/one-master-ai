from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.models.user import User
from app.services.master_agent import master_agent

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    skill: str
    answer: str


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
):
    # Profesi diambil dari profil user yang login (bukan dari client)
    result = master_agent(
        user_message=data.message, profession=current_user.profession
    )
    return ChatResponse(skill=result["skill"], answer=result["answer"])
