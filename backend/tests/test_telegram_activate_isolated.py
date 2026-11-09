import os
import tempfile
import uuid
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from fastapi import HTTPException

fd, db_path = tempfile.mkstemp(
    prefix="one_master_telegram_activate_",
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


class TelegramActivateTests(unittest.TestCase):

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

        self.user = User(
            id=str(uuid.uuid4()),
            email=f"activate-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pengguna Tes",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.account = TelegramAccount(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            bot_id=920001,
            bot_username="bot_aktivasi_tes",
            bot_token_encrypted="test-encrypted-value",
            bot_token_hash=f"test-hash-{uuid.uuid4().hex}",
            webhook_secret=f"test-secret-{uuid.uuid4().hex}",
            status="approved",
        )
        self.db.add(self.account)
        self.db.commit()

        identity["user"] = self.user

    def tearDown(self):
        self.db.close()

    def fake_webhook_info(self, url=""):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "ok": True,
            "result": {"url": url},
        }
        return response

    def test_01_activation_saves_link_code_before_set_webhook(self):
        def verify_before_set_webhook(token, method, **kwargs):
            self.assertEqual(method, "setWebhook")

            # Periksa melalui koneksi database terpisah.
            # Ini membuktikan kode sudah di-commit sebelum setWebhook.
            with Session() as verify_db:
                saved = verify_db.get(
                    TelegramAccount,
                    self.account.id,
                )
                self.assertIsNotNone(saved.link_code_hash)
                self.assertEqual(len(saved.link_code_hash), 64)
                self.assertIsNotNone(saved.link_code_expires_at)

            self.assertIn("/api/v1/telegram/webhook/", kwargs["url"])
            self.assertEqual(
                kwargs["secret_token"],
                self.account.webhook_secret,
            )
            return {"url": kwargs["url"]}

        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-test-token",
        ), patch(
            "app.api.routes_telegram.httpx.get",
            return_value=self.fake_webhook_info(),
        ), patch(
            "app.api.routes_telegram.telegram_api",
            side_effect=verify_before_set_webhook,
        ) as mock_set_webhook:

            response = self.client.post("/api/v1/telegram/activate")

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["status"], "waiting_for_owner")
        self.assertEqual(data["bot_username"], "bot_aktivasi_tes")
        self.assertEqual(data["expires_in_seconds"], 900)
        self.assertIn("https://t.me/bot_aktivasi_tes?start=", data["link_url"])
        mock_set_webhook.assert_called_once()

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account.id)
        self.assertIsNotNone(saved.link_code_hash)
        self.assertIsNotNone(saved.link_code_expires_at)

        print("LULUS: kode linking tersimpan sebelum webhook dipasang")

    def test_02_existing_different_webhook_is_not_overwritten(self):
        old_url = "https://example.invalid/old-webhook"

        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-test-token",
        ), patch(
            "app.api.routes_telegram.httpx.get",
            return_value=self.fake_webhook_info(old_url),
        ), patch(
            "app.api.routes_telegram.telegram_api",
        ) as mock_set_webhook:

            response = self.client.post("/api/v1/telegram/activate")

        self.assertEqual(response.status_code, 409)
        mock_set_webhook.assert_not_called()

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account.id)
        self.assertIsNone(saved.link_code_hash)
        self.assertIsNone(saved.link_code_expires_at)

        print("LULUS: webhook lama tidak ditimpa")

    def test_03_failed_set_webhook_clears_link_code(self):
        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-test-token",
        ), patch(
            "app.api.routes_telegram.httpx.get",
            return_value=self.fake_webhook_info(),
        ), patch(
            "app.api.routes_telegram.telegram_api",
            side_effect=HTTPException(
                status_code=502,
                detail="Simulasi kegagalan Telegram",
            ),
        ):

            response = self.client.post("/api/v1/telegram/activate")

        self.assertEqual(response.status_code, 502)

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account.id)

        self.assertIsNone(saved.link_code_hash)
        self.assertIsNone(saved.link_code_expires_at)
        self.assertEqual(saved.last_error, "set_webhook_failed")

        print("LULUS: kode linking dibersihkan saat setWebhook gagal")


if __name__ == "__main__":
    unittest.main()
