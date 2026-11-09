from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_conversations import router as conversations_router
from app.api.routes_documents import router as documents_router
from app.api.routes_files import router as files_router
from app.api.routes_admin import router as admin_router
from app.api.routes_topup import router as topup_router
from app.api.routes_telegram import router as telegram_router
from app.core.config import APP_NAME, APP_VERSION, API_DOCS_PUBLIC
from app.models.conversation import Conversation, Message  # noqa: F401
from app.models.database import Base, engine
from app.models.subscription import (  # noqa: F401
    SubscriptionPlan,
    UserSubscription,
    TokenWallet,
    TokenTransaction,
)
from app.models.topup import TopUpRequest  # noqa: F401
from app.models.token_usage import TokenUsage  # noqa: F401
from app.models.token_reservation import TokenReservation  # noqa: F401
from app.models.token_reservation_result import TokenReservationResult  # noqa: F401
from app.models.telegram_account import TelegramAccount  # noqa: F401
from app.models.telegram_update import TelegramUpdate  # noqa: F401
from app.models.user import User  # noqa: F401 — pastikan model terdaftar

# Buat tabel otomatis saat startup (untuk tabel baru seperti users_jwt)
Base.metadata.create_all(bind=engine)

# Dokumentasi API (/docs, /openapi.json) hanya terbuka jika API_DOCS_PUBLIC=true.
# Default: TERTUTUP untuk publik (produksi). Set true hanya saat development.
if API_DOCS_PUBLIC:
    app = FastAPI(
        title=APP_NAME,
        description="Asisten AI multi-skill: akademik, pemerintahan, bisnis, teknologi, riset.",
        version=APP_VERSION,
    )
else:
    app = FastAPI(
        title=APP_NAME,
        version=APP_VERSION,
        docs_url=None,       # /docs dimatikan
        redoc_url=None,      # /redoc dimatikan
        openapi_url=None,    # /openapi.json dimatikan
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
app.include_router(documents_router)
app.include_router(files_router)
app.include_router(admin_router)
app.include_router(topup_router)
app.include_router(telegram_router)

from app.api.proxy import router as proxy_router
app.include_router(proxy_router, prefix="/api/v1", tags=["OpenAI Proxy"])

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
