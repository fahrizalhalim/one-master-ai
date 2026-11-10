import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

fd, db_path = tempfile.mkstemp(prefix="one_master_tg_crud_", suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.main import app
from app.models.database import Base, get_db
from app.models.user import User
from app.models.telegram_account import TelegramAccount
from app.api.routes_telegram import _extract_media, _user_dir

engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine, autoflush=False)


def override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()


class TelegramFileCrudTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.prev = app.dependency_overrides.copy()
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(cls.prev)
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        self.db = Session()

        self.user = User(
            id=str(uuid.uuid4()),
            email=f"crud-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pemilik File",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.account = TelegramAccount(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            bot_id=920002,
            bot_username="bot_crud_tes",
            bot_token_encrypted="encrypted-test-only",
            bot_token_hash=f"hash-{uuid.uuid4().hex}",
            webhook_secret="secret-crud-test-123",
            status="active",
            telegram_owner_id=700002,
            telegram_owner_username="pemilik_file",
        )
        self.db.add(self.account)
        self.db.commit()

        self.url = f"/api/v1/telegram/webhook/{self.account.id}"
        self.headers = {"X-Telegram-Bot-Api-Secret-Token": "secret-crud-test-123"}

    def tearDown(self):
        self.db.close()

    def _message(self, update_id, text=None, document=None, caption=None):
        msg = {
            "message_id": update_id,
            "from": {"id": 700002, "username": "pemilik_file"},
            "chat": {"id": 700002, "type": "private"},
        }
        if text is not None:
            msg["text"] = text
        if document is not None:
            msg["document"] = document
        if caption is not None:
            msg["caption"] = caption
        return {"update_id": update_id, "message": msg}

    def test_extract_media_document(self):
        media = _extract_media({
            "document": {"file_id": "AAA", "file_name": "laporan.pdf", "file_size": 100}
        })
        self.assertEqual(media["ext"], ".pdf")
        self.assertEqual(media["file_name"], "laporan.pdf")

    def test_extract_media_photo_picks_largest(self):
        media = _extract_media({
            "photo": [
                {"file_id": "S", "file_size": 10},
                {"file_id": "L", "file_size": 500},
            ]
        })
        self.assertEqual(media["file_id"], "L")
        self.assertEqual(media["ext"], ".jpg")

    def test_extract_media_unsupported_returns_none(self):
        self.assertIsNone(_extract_media({"text": "hai"}))

    def test_delete_command_removes_file(self):
        user_dir = _user_dir(self.user.id)
        user_dir.mkdir(parents=True, exist_ok=True)
        fid = "abcdef123456"
        (user_dir / f"{fid}.png").write_bytes(b"data")
        (user_dir / f"{fid}.name.txt").write_text("foto.png", encoding="utf-8")

        with patch(
            "app.api.routes_telegram.decrypt_bot_token", return_value="fake-token"
        ), patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new=AsyncMock(return_value=True),
        ) as send:
            resp = self.client.post(
                self.url, headers=self.headers,
                json=self._message(5001, text=f"/delete {fid}"),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("command"), "delete")
        self.assertFalse((user_dir / f"{fid}.png").exists())
        self.assertFalse((user_dir / f"{fid}.name.txt").exists())
        send.assert_awaited_once()

    def test_update_command_replaces_file(self):
        user_dir = _user_dir(self.user.id)
        user_dir.mkdir(parents=True, exist_ok=True)
        fid = "abcdefabcdef"
        (user_dir / f"{fid}.docx").write_bytes(b"lama")
        (user_dir / f"{fid}.name.txt").write_text("lama.docx", encoding="utf-8")

        async def fake_download(token, document, user_id):
            new_id = "fedcbafedcba"
            (user_dir / f"{new_id}.docx").write_bytes(b"baru")
            (user_dir / f"{new_id}.name.txt").write_text("baru.docx", encoding="utf-8")
            (user_dir / f"{new_id}.extract.txt").write_text("isi baru", encoding="utf-8")
            return new_id, "baru.docx", "isi baru", 4

        with patch(
            "app.api.routes_telegram.decrypt_bot_token", return_value="fake-token"
        ), patch(
            "app.api.routes_telegram._download_telegram_document",
            new=AsyncMock(side_effect=fake_download),
        ), patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new=AsyncMock(return_value=True),
        ):
            resp = self.client.post(
                self.url, headers=self.headers,
                json=self._message(
                    5002,
                    document={"file_id": "ZZZ", "file_name": "baru.docx", "file_size": 4},
                    caption=f"/update {fid}",
                ),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("command"), "update")
        self.assertTrue((user_dir / f"{fid}.docx").exists())
        self.assertEqual((user_dir / f"{fid}.docx").read_bytes(), b"baru")
        self.assertFalse((user_dir / "fedcbafedcba.docx").exists())
        self.assertEqual(
            (user_dir / f"{fid}.name.txt").read_text(encoding="utf-8"), "baru.docx"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
