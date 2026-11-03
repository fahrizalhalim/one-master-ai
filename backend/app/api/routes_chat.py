from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.master_agent import master_agent

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    profession: str = Field(default="umum", max_length=100)
    user_id: str = Field(default="anonymous", max_length=64)


class ChatResponse(BaseModel):
    skill: str
    answer: str


@router.post("/chat", response_model=ChatResponse)
def chat(data: ChatRequest):
    result = master_agent(user_message=data.message, profession=data.profession)
    return ChatResponse(skill=result["skill"], answer=result["answer"])
