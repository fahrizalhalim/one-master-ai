"""Tes riwayat percakapan (memory) dengan SQLite in-memory."""
import os

os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_conv.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.models.database import Base, engine, SessionLocal
from app.models.user import User  # noqa: E402

client = TestClient(app)

EMAIL = "mem@test.ac.id"
PASSWORD = "password123"


def setup_function():
    Base.metadata.create_all(bind=engine)


def _approve_all():
    """Setujui semua user pending langsung via DB (test helper)."""
    db = SessionLocal()
    try:
        db.query(User).filter(User.status == "pending").update(
            {"status": "active"}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _token() -> str:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "profession": "dosen"},
    )
    if r.status_code != 201:
        r = client.post(
            "/api/v1/auth/login",
            data={"username": EMAIL, "password": PASSWORD},
        )
    _approve_all()
    return r.json()["access_token"]


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def test_chat_creates_conversation_and_history():
    t = _token()
    # Chat pertama — buat percakapan baru.
    # Dengan dummy key, pemanggilan AI gagal -> 502, TAPI percakapan
    # dan pesan user tetap tersimpan (desain memory anti-hilang).
    r1 = client.post(
        "/api/v1/chat", json={"message": "halo"}, headers=_auth(t)
    )
    assert r1.status_code in (200, 502)
    # conversation_id tidak ada di body 502; ambil dari list conversations
    r_list = client.get("/api/v1/conversations", headers=_auth(t))
    assert r_list.status_code == 200
    items = r_list.json()
    assert len(items) >= 1
    cid = items[0]["id"]
    assert items[0]["title"].startswith("halo")

    # Detail percakapan: minimal 1 pesan (user)
    r3 = client.get(f"/api/v1/conversations/{cid}", headers=_auth(t))
    assert r3.status_code == 200
    detail = r3.json()
    assert any(m["role"] == "user" and m["content"] == "halo" for m in detail["messages"])


def test_conversation_isolation():
    # user A buat percakapan
    t = _token()
    r = client.post("/api/v1/chat", json={"message": "rahasia A"}, headers=_auth(t))
    assert r.status_code in (200, 502)
    r_list = client.get("/api/v1/conversations", headers=_auth(t))
    cid = r_list.json()[0]["id"]

    # user B tidak boleh lihat
    r2 = client.post(
        "/api/v1/auth/register",
        json={"email": "b@test.ac.id", "password": "password123"},
    )
    t2 = r2.json()["access_token"]
    r3 = client.get(f"/api/v1/conversations/{cid}", headers=_auth(t2))
    assert r3.status_code == 404

    # delete milik user B ke percakapan A juga 404
    r4 = client.delete(f"/api/v1/conversations/{cid}", headers=_auth(t2))
    assert r4.status_code == 404


def test_delete_conversation():
    t = _token()
    r = client.post("/api/v1/chat", json={"message": "hapus aku"}, headers=_auth(t))
    assert r.status_code in (200, 502)
    r_list = client.get("/api/v1/conversations", headers=_auth(t))
    cid = r_list.json()[0]["id"]
    r2 = client.delete(f"/api/v1/conversations/{cid}", headers=_auth(t))
    assert r2.status_code == 200
    r3 = client.get(f"/api/v1/conversations/{cid}", headers=_auth(t))
    assert r3.status_code == 404
