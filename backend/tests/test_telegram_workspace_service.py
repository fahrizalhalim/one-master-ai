import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.models.user import User
from app.services import telegram_workspace_service as service
from app.services.workspace_service import WorkspaceError


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()

    upload_root = tmp_path / "uploads"
    upload_root.mkdir()

    monkeypatch.setattr(service, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(service, "OMAI_TELEGRAM_BOT_TOKEN", "test-token-only")

    user = User(
        id=str(uuid.uuid4()),
        email=f"{uuid.uuid4()}@test.invalid",
        password_hash="test-only",
        full_name="Pengguna Tes",
        role="user",
        status="active",
        is_active=True,
        created_at=datetime(2026, 11, 1, 0, 0, 0),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    try:
        yield db, user, upload_root
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeDownload:
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size=65536):
        for chunk in self.chunks:
            yield chunk


class FakeTelegramClient:
    def __init__(self, chunks, remote_path="documents/test.txt"):
        self.chunks = chunks
        self.remote_path = remote_path

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        return FakeResponse({
            "ok": True,
            "result": {"file_path": self.remote_path},
        })

    def stream(self, method, url):
        return FakeDownload(self.chunks)


def install_fake_client(monkeypatch, chunks, remote_path="documents/test.txt"):
    monkeypatch.setattr(
        service.httpx,
        "AsyncClient",
        lambda **kwargs: FakeTelegramClient(chunks, remote_path),
    )


@pytest.mark.anyio
async def test_valid_txt_document_is_saved(workspace_env, monkeypatch):
    db, user, upload_root = workspace_env
    content = b"Dokumen uji Telegram"
    install_fake_client(monkeypatch, [content])

    result = await service.upload_telegram_document(
        db,
        user,
        {
            "file_id": "telegram-file-id",
            "file_name": "catatan.txt",
            "file_size": len(content),
        },
    )

    user_dir = upload_root / user.id
    originals = list(user_dir.glob("*.txt"))
    original_files = [
        path for path in originals
        if not path.name.endswith((".extract.txt", ".name.txt"))
    ]

    assert "File berhasil diunggah" in result
    assert len(original_files) == 1
    assert original_files[0].read_bytes() == content
    assert original_files[0].with_name(
        f"{original_files[0].stem}.name.txt"
    ).read_text() == "catatan.txt"


@pytest.mark.anyio
async def test_declared_size_over_limit_is_rejected(workspace_env, monkeypatch):
    db, user, _ = workspace_env
    install_fake_client(monkeypatch, [b"tidak boleh diunduh"])

    with pytest.raises(WorkspaceError, match="maksimal 5 MB"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "besar.txt",
                "file_size": service.MAX_TELEGRAM_DOWNLOAD + 1,
            },
        )


@pytest.mark.anyio
async def test_invalid_declared_size_is_rejected(workspace_env, monkeypatch):
    db, user, _ = workspace_env
    install_fake_client(monkeypatch, [b"konten"])

    with pytest.raises(WorkspaceError, match="ukuran file"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "uji.txt",
                "file_size": "bukan-angka",
            },
        )


@pytest.mark.anyio
async def test_stream_over_limit_is_rejected_without_saving(workspace_env, monkeypatch):
    db, user, upload_root = workspace_env
    install_fake_client(
        monkeypatch,
        [b"a" * (service.MAX_TELEGRAM_DOWNLOAD - 10), b"b" * 20],
    )

    with pytest.raises(WorkspaceError, match="maksimal 5 MB"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "besar.txt",
            },
        )

    assert not (upload_root / user.id).exists()


@pytest.mark.anyio
async def test_suspicious_remote_path_is_rejected(workspace_env, monkeypatch):
    db, user, upload_root = workspace_env
    install_fake_client(monkeypatch, [b"konten"], "../outside.txt")

    with pytest.raises(WorkspaceError, match="Lokasi file"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "uji.txt",
            },
        )

    assert not (upload_root / user.id).exists()


@pytest.mark.anyio
async def test_empty_document_is_rejected(workspace_env, monkeypatch):
    db, user, upload_root = workspace_env
    install_fake_client(monkeypatch, [])

    with pytest.raises(WorkspaceError, match="kosong"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "kosong.txt",
                "file_size": 0,
            },
        )

    assert not (upload_root / user.id).exists()


@pytest.mark.anyio
async def test_unsupported_extension_is_rejected(workspace_env, monkeypatch):
    db, user, upload_root = workspace_env
    install_fake_client(monkeypatch, [b"konten"])

    with pytest.raises(WorkspaceError, match="Format belum didukung"):
        await service.upload_telegram_document(
            db,
            user,
            {
                "file_id": "telegram-file-id",
                "file_name": "program.exe",
            },
        )

    assert not (upload_root / user.id).exists()
