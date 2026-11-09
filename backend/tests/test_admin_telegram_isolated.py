import os
import tempfile
import uuid
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# Database sementara: tidak menggunakan database production.
fd, db_path = tempfile.mkstemp(
    prefix="one_master_telegram_admin_",
    suffix=".db",
)
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.main import app
from app.api.dependencies import get_current_user
from app.models.database import Base, get_db
from app.models.user import User
from app.models.telegram_account import TelegramAccount

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)

identity = {"user": None}


def override_current_user():
    return identity["user"]


def override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_current_user] = override_current_user
app.dependency_overrides[get_db] = override_get_db


class TelegramAdminApprovalTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        self.db = Session()

        self.admin = User(
            id=str(uuid.uuid4()),
            email=f"admin-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Admin Tes",
            role="admin",
            status="active",
            is_active=True,
        )

        self.regular_user = User(
            id=str(uuid.uuid4()),
            email=f"user-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pengguna Tes",
            role="user",
            status="active",
            is_active=True,
        )

        self.owner1 = User(
            id=str(uuid.uuid4()),
            email=f"owner1-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pemilik Bot Satu",
            role="user",
            status="active",
            is_active=True,
        )

        self.owner2 = User(
            id=str(uuid.uuid4()),
            email=f"owner2-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pemilik Bot Dua",
            role="user",
            status="active",
            is_active=True,
        )

        self.db.add_all([
            self.admin,
            self.regular_user,
            self.owner1,
            self.owner2,
        ])
        self.db.flush()

        self.account1 = self.make_account(self.owner1, 910001, "bot_tes_satu")
        self.account2 = self.make_account(self.owner2, 910002, "bot_tes_dua")

        self.db.add_all([self.account1, self.account2])
        self.db.commit()

        identity["user"] = self.admin

    def tearDown(self):
        self.db.close()

    def make_account(self, owner, bot_id, username):
        return TelegramAccount(
            id=str(uuid.uuid4()),
            user_id=owner.id,
            bot_id=bot_id,
            bot_username=username,
            bot_token_encrypted="test-encrypted-value",
            bot_token_hash=f"test-hash-{uuid.uuid4().hex}",
            webhook_secret=f"test-secret-{uuid.uuid4().hex}",
            status="pending",
        )

    def test_01_admin_can_list_pending_accounts_without_secrets(self):
        response = self.client.get("/api/v1/admin/telegram/pending")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(len(data), 2)
        self.assertEqual(
            {item["bot_username"] for item in data},
            {"bot_tes_satu", "bot_tes_dua"},
        )

        for item in data:
            self.assertNotIn("bot_token_encrypted", item)
            self.assertNotIn("bot_token_hash", item)
            self.assertNotIn("webhook_secret", item)

        print("LULUS: admin melihat daftar pending tanpa membocorkan secret")

    def test_02_regular_user_is_forbidden(self):
        identity["user"] = self.regular_user

        response = self.client.get("/api/v1/admin/telegram/pending")

        self.assertEqual(response.status_code, 403)
        print("LULUS: pengguna biasa ditolak dengan HTTP 403")

    def test_03_admin_can_approve_and_cannot_repeat(self):
        url = f"/api/v1/admin/telegram/{self.account1.id}/decision"

        response = self.client.post(url, json={"decision": "approve"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account1.id)
        self.assertEqual(saved.status, "approved")

        repeated = self.client.post(url, json={"decision": "approve"})
        self.assertEqual(repeated.status_code, 409)

        print("LULUS: persetujuan tersimpan dan pemrosesan ulang ditolak")

    def test_04_admin_can_reject_account(self):
        url = f"/api/v1/admin/telegram/{self.account2.id}/decision"

        response = self.client.post(url, json={"decision": "reject"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "rejected")

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account2.id)
        self.assertEqual(saved.status, "rejected")

        print("LULUS: penolakan tersimpan di database")

    def test_05_unknown_account_returns_404(self):
        url = f"/api/v1/admin/telegram/{uuid.uuid4()}/decision"

        response = self.client.post(url, json={"decision": "approve"})

        self.assertEqual(response.status_code, 404)
        print("LULUS: ID koneksi yang tidak ada menghasilkan HTTP 404")

    def test_06_invalid_decision_returns_422(self):
        url = f"/api/v1/admin/telegram/{self.account1.id}/decision"

        response = self.client.post(url, json={"decision": "activate"})

        self.assertEqual(response.status_code, 422)
        print("LULUS: keputusan di luar approve/reject ditolak")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)
