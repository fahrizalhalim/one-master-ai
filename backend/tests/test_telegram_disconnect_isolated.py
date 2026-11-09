import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

fd, db_path = tempfile.mkstemp(
    prefix="one_master_telegram_disconnect_",
    suffix=".db",
)
os.close(fd)

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.main import app
from app.api.dependencies import get_current_user
from app.models.database import Base, get_db
from app.models.user import User
from app.models.telegram_account import TelegramAccount
from app.models.telegram_update import TelegramUpdate
from app.models.conversation import Conversation, Message

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(engine, "connect")
def enable_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

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


class TelegramDisconnectTests(unittest.TestCase):

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
            email=f"disconnect-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pengguna Tes",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            title="Percakapan yang harus tetap ada",
        )
        self.db.add(self.conversation)
        self.db.flush()

        self.account = TelegramAccount(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            bot_id=930001,
            bot_username="bot_disconnect_tes",
            bot_token_encrypted="encrypted-test-only",
            bot_token_hash=f"hash-{uuid.uuid4().hex}",
            webhook_secret=f"secret-{uuid.uuid4().hex}",
            conversation_id=self.conversation.id,
            status="active",
            telegram_owner_id=700001,
        )
        self.db.add(self.account)
        self.db.flush()

        self.update = TelegramUpdate(
            id=str(uuid.uuid4()),
            account_id=self.account.id,
            update_id=990001,
            status="completed",
        )
        self.message = Message(
            id=str(uuid.uuid4()),
            conversation_id=self.conversation.id,
            role="user",
            skill="",
            content="Pesan yang harus tetap ada",
        )

        self.db.add_all([self.update, self.message])
        self.db.commit()

        identity["user"] = self.user

    def tearDown(self):
        self.db.close()

    def test_01_account_not_found_returns_404(self):
        self.db.delete(self.account)
        self.db.commit()

        response = self.client.delete("/api/v1/telegram/disconnect")

        self.assertEqual(response.status_code, 404)
        print("LULUS: akun yang tidak ditemukan menghasilkan 404")

    def test_02_telegram_failure_preserves_local_data(self):
        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-test-token",
        ), patch(
            "app.api.routes_telegram.telegram_api",
            side_effect=HTTPException(
                status_code=502,
                detail="Simulasi kegagalan Telegram",
            ),
        ) as mock_api:

            response = self.client.delete(
                "/api/v1/telegram/disconnect"
            )

        self.assertEqual(response.status_code, 502)
        mock_api.assert_called_once_with(
            "fake-test-token",
            "deleteWebhook",
            drop_pending_updates=False,
        )

        self.db.expire_all()
        self.assertIsNotNone(
            self.db.get(TelegramAccount, self.account.id)
        )
        self.assertIsNotNone(
            self.db.get(TelegramUpdate, self.update.id)
        )
        self.assertIsNotNone(
            self.db.get(Conversation, self.conversation.id)
        )
        self.assertIsNotNone(
            self.db.get(Message, self.message.id)
        )

        print("LULUS: kegagalan Telegram tidak menghapus data lokal")

    def test_03_success_deletes_account_and_updates_only(self):
        account_id = self.account.id
        update_id = self.update.id
        conversation_id = self.conversation.id
        message_id = self.message.id

        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-test-token",
        ), patch(
            "app.api.routes_telegram.telegram_api",
            return_value={"ok": True},
        ) as mock_api:

            response = self.client.delete(
                "/api/v1/telegram/disconnect"
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["deleted"])

        mock_api.assert_called_once_with(
            "fake-test-token",
            "deleteWebhook",
            drop_pending_updates=False,
        )

        self.db.expire_all()

        self.assertIsNone(
            self.db.get(TelegramAccount, account_id)
        )
        self.assertIsNone(
            self.db.get(TelegramUpdate, update_id)
        )
        self.assertIsNotNone(
            self.db.get(Conversation, conversation_id)
        )
        self.assertIsNotNone(
            self.db.get(Message, message_id)
        )

        print(
            "LULUS: akun dan update terhapus, "
            "percakapan dan pesan tetap tersimpan"
        )


if __name__ == "__main__":
    unittest.main()
