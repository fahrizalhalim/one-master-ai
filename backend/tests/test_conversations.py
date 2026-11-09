"""Tes riwayat percakapan dengan data pengguna unik."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.models.database import Base, engine, SessionLocal
from app.models.user import User
from app.models.subscription import TokenWallet

client = TestClient(app)
PASSWORD = "password123"


def _new_email():
    return f"conversation-{uuid.uuid4().hex}@test.ac.id"


def _register(email):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "profession": "dosen",
        },
    )
    assert response.status_code == 201, response.text

    token = response.json()["access_token"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        user.status = "active"

        wallet = (
            db.query(TokenWallet)
            .filter(TokenWallet.user_id == user.id)
            .first()
        )
        if wallet is None:
            db.add(TokenWallet(
                user_id=user.id,
                topup_balance=100000,
            ))

        db.commit()
    finally:
        db.close()

    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_chat(token, message):
    response = client.post(
        "/api/v1/chat",
        json={"message": message},
        headers=_auth(token),
    )
    assert response.status_code in (200, 502), response.text
    return response


def _find_conversation(token, title):
    response = client.get(
        "/api/v1/conversations",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text

    matches = [
        item for item in response.json()
        if item["title"].startswith(title)
    ]
    assert len(matches) == 1, response.json()
    return matches[0]


def test_chat_creates_conversation_and_history():
    Base.metadata.create_all(bind=engine)

    token = _register(_new_email())
    _create_chat(token, "halo")

    item = _find_conversation(token, "halo")
    detail = client.get(
        f"/api/v1/conversations/{item['id']}",
        headers=_auth(token),
    )

    assert detail.status_code == 200, detail.text
    assert any(
        message["role"] == "user"
        and message["content"] == "halo"
        for message in detail.json()["messages"]
    )


def test_conversation_isolation():
    Base.metadata.create_all(bind=engine)

    token_a = _register(_new_email())
    token_b = _register(_new_email())

    _create_chat(token_a, "rahasia-A")
    item = _find_conversation(token_a, "rahasia-A")

    response = client.get(
        f"/api/v1/conversations/{item['id']}",
        headers=_auth(token_b),
    )
    assert response.status_code == 404

    response = client.delete(
        f"/api/v1/conversations/{item['id']}",
        headers=_auth(token_b),
    )
    assert response.status_code == 404


def test_delete_conversation():
    Base.metadata.create_all(bind=engine)

    token = _register(_new_email())
    _create_chat(token, "hapus-aku")
    item = _find_conversation(token, "hapus-aku")

    response = client.delete(
        f"/api/v1/conversations/{item['id']}",
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text

    response = client.get(
        f"/api/v1/conversations/{item['id']}",
        headers=_auth(token),
    )
    assert response.status_code == 404
