from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_conversations import router as conversations_router
from app.core.config import APP_NAME, APP_VERSION
from app.models.conversation import Conversation, Message  # noqa: F401
from app.models.database import Base, engine
from app.models.user import User  # noqa: F401 — pastikan model terdaftar

# Buat tabel otomatis saat startup (untuk tabel baru seperti users_jwt)
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=APP_NAME,
    description="Asisten AI multi-skill: akademik, pemerintahan, bisnis, teknologi, riset.",
    version=APP_VERSION,
)

# CORS — izinkan frontend dev server & domain produksi
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://gamalama-mht.my.id",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conversations_router)


@app.get("/")
def home():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "status": "running",
    }


@app.get("/health")
def health():
    return {"status": "healthy"}
