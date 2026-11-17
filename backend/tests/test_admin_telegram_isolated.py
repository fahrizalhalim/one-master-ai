import os
import tempfile
import uuid
import unittest
from unittest.mock import patch
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
from app.services.auth_service import hash_password
from app.models.telegram_central_update import TelegramCentralUpdate
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

TEST_ADMIN_PASSWORD = "OMAI-Test-Admin-2026"
TEST_ADMIN_PASSWORD_HASH = hash_password(TEST_ADMIN_PASSWORD)


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
            password_hash=TEST_ADMIN_PASSWORD_HASH,
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

        def fake_activate(db, account, approve_pending=False):
            self.assertTrue(approve_pending)
            account.status = "approved"
            db.commit()

        with patch(
            "app.api.routes_admin.activate_telegram_account",
            side_effect=fake_activate,
        ):
            response = self.client.post(
                url,
                json={"decision": "approve"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "approved")

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account1.id)
        self.assertEqual(saved.status, "approved")

        repeated = self.client.post(
            url,
            json={"decision": "approve"},
        )
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


    def make_central_update(
        self,
        update_id,
        status="needs_review",
        error_code="telegram_delivery_uncertain",
    ):
        update = TelegramCentralUpdate(
            update_id=update_id,
            telegram_user_id=920001,
            telegram_message_id=930001,
            user_id=self.owner1.id,
            status=status,
            error_code=error_code,
        )
        self.db.add(update)
        self.db.commit()
        return update

    def test_07_admin_can_see_uncertain_delivery(self):
        self.make_central_update(940001)

        response = self.client.get(
            "/api/v1/admin/telegram-central/review"
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)

        item = data["items"][0]
        self.assertEqual(item["update_id"], 940001)
        self.assertEqual(item["error_code"], "telegram_delivery_uncertain")
        self.assertEqual(item["status"], "needs_review")

    def test_08_regular_user_cannot_access_central_review(self):
        self.make_central_update(940002)
        identity["user"] = self.regular_user

        response = self.client.get(
            "/api/v1/admin/telegram-central/review"
        )

        self.assertEqual(response.status_code, 403)

    def test_09_uncertain_delivery_cannot_be_retried(self):
        from types import SimpleNamespace
        from app.api import routes_telegram_central

        self.make_central_update(940003)
        fake_connection = SimpleNamespace(info={})

        with patch.object(
            routes_telegram_central,
            "_acquire_delivery_lock",
            return_value=fake_connection,
        ), patch.object(
            routes_telegram_central,
            "_release_delivery_lock",
        ) as release_lock, patch.object(
            routes_telegram_central,
            "_deliver_saved_reply_locked",
        ) as deliver:
            response = self.client.post(
                "/api/v1/admin/telegram-central/940003/retry-delivery",
                json={"admin_password": TEST_ADMIN_PASSWORD},
            )

        self.assertEqual(response.status_code, 409)
        deliver.assert_not_called()
        release_lock.assert_called_once()


    def post_central_set_webhook(self, fake_api, base_url="https://gamalama-mht.my.id"):
        from app.api import routes_admin

        with patch.object(routes_admin, "OMAI_TELEGRAM_BOT_TOKEN", "test-central-token"), \
             patch.object(routes_admin, "OMAI_TELEGRAM_BOT_USERNAME", "OnMasterAI_Bot"), \
             patch.object(routes_admin, "OMAI_TELEGRAM_WEBHOOK_SECRET", "test-webhook-secret"), \
             patch.object(routes_admin, "TELEGRAM_WEBHOOK_BASE_URL", base_url), \
             patch.object(routes_admin, "telegram_api", side_effect=fake_api):
            return self.client.post(
                "/api/v1/admin/telegram-central/set-webhook",
                json={"admin_password": TEST_ADMIN_PASSWORD},
            )

    def test_10_central_webhook_can_be_set_and_verified(self):
        calls = []

        def fake_api(token, method, **kwargs):
            calls.append((method, kwargs))

            if method == "getMe":
                return {"username": "OnMasterAI_Bot"}

            if method == "getWebhookInfo":
                count = sum(1 for item in calls if item[0] == "getWebhookInfo")
                if count == 1:
                    return {"url": ""}
                return {
                    "url": "https://gamalama-mht.my.id/api/v1/telegram-central/webhook",
                    "pending_update_count": 0,
                    "allowed_updates": ["message"],
                }

            if method == "setWebhook":
                return True

            raise AssertionError(f"Metode Telegram tidak terduga: {method}")

        response = self.post_central_set_webhook(fake_api)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

        set_calls = [item for item in calls if item[0] == "setWebhook"]
        self.assertEqual(len(set_calls), 1)

        kwargs = set_calls[0][1]
        self.assertEqual(
            kwargs["url"],
            "https://gamalama-mht.my.id/api/v1/telegram-central/webhook",
        )
        self.assertEqual(kwargs["secret_token"], "test-webhook-secret")
        self.assertEqual(kwargs["allowed_updates"], ["message"])
        self.assertFalse(kwargs["drop_pending_updates"])

        print("LULUS: webhook pusat dipasang dan diverifikasi melalui mock")

    def test_11_wrong_bot_identity_is_rejected(self):
        calls = []

        def fake_api(token, method, **kwargs):
            calls.append(method)
            if method == "getMe":
                return {"username": "BotLain"}
            raise AssertionError("Seharusnya tidak melanjutkan pemeriksaan webhook")

        response = self.post_central_set_webhook(fake_api)

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("setWebhook", calls)

        print("LULUS: identitas bot yang salah ditolak")

    def test_12_existing_different_webhook_is_not_overwritten(self):
        calls = []

        def fake_api(token, method, **kwargs):
            calls.append(method)
            if method == "getMe":
                return {"username": "OnMasterAI_Bot"}
            if method == "getWebhookInfo":
                return {"url": "https://other.example/webhook"}
            raise AssertionError("Webhook berbeda tidak boleh ditimpa")

        response = self.post_central_set_webhook(fake_api)

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("setWebhook", calls)

        print("LULUS: webhook lama dengan URL berbeda tidak ditimpa")

    def test_13_wrong_admin_password_is_rejected(self):
        from app.api import routes_admin

        with patch.object(routes_admin, "telegram_api") as telegram_mock:
            response = self.client.post(
                "/api/v1/admin/telegram-central/set-webhook",
                json={"admin_password": "password-salah"},
            )

        self.assertEqual(response.status_code, 401)
        telegram_mock.assert_not_called()

        print("LULUS: password Admin salah ditolak sebelum menghubungi Telegram")

    def test_14_unapproved_base_url_is_rejected(self):
        calls = []

        def fake_api(token, method, **kwargs):
            calls.append(method)
            raise AssertionError("Telegram tidak boleh dihubungi")

        response = self.post_central_set_webhook(
            fake_api,
            base_url="https://evil.example",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [])

        print("LULUS: URL dasar di luar domain resmi ditolak")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)
