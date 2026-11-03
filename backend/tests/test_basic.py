import os

# Pastikan API key tersedia untuk test; jika tidak ada, pakai dummy.
os.environ.setdefault("OPENAI_API_KEY", "test-key-dummy")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.master_agent import load_skill_instruction  # noqa: E402
from app.skills.skill_router import route_skill  # noqa: E402

client = TestClient(app)


def test_home():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["app"] == "ONE MASTER AI"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_route_academic():
    assert route_skill("tolong buatkan RPS mata kuliah dengan OBE") == "academic"


def test_route_business():
    assert route_skill("saya mau bikin rencana bisnis UMKM") == "business"


def test_route_technology():
    assert route_skill("buatkan website dengan database") == "technology"


def test_route_general():
    assert route_skill("halo apa kabar") == "general"


def test_load_skill_instruction():
    assert "OBE" in load_skill_instruction("academic")


def test_chat_validation():
    r = client.post("/api/v1/chat", json={"message": ""})
    assert r.status_code == 422  # pesan kosong ditolak
