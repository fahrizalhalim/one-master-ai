"""Pengujian terisolasi Telegram Bot Pusat ONE MASTER AI.

Tidak menggunakan database produksi, Telegram API, atau AI provider.
Jalankan hanya setelah isi file diperiksa.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# Paksa konfigurasi aplikasi menggunakan lokasi tes sementara
# sebelum modul aplikasi dan database diimpor.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="omai-telegram-central-test-"))
_TEST_DB = _TEST_DIR / "central-test.db"

# Saat pytest, gunakan environment SQLite dari conftest.py.
# Saat dijalankan mandiri, paksa database dan storage sementara.
if "pytest" not in sys.modules:
    os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
    os.environ["DOC_STORAGE"] = str(_TEST_DIR / "storage")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request
from fastapi import HTTPException

# Import modul agar seluruh model dan relasi foreign key terdaftar.
import app.models.subscription
from app.models.database import Base
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.models.telegram_central_link import TelegramCentralLink
from app.models.telegram_central_update import TelegramCentralUpdate
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.api import routes_telegram_central as central
from app.api.routes_chat import ChatResponse


# Engine khusus tes. Tidak menggunakan engine aplikasi.
engine = create_engine(
    f"sqlite:///{_TEST_DB}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)


def make_request(payload):
    body = json.dumps(payload).encode()

    async def receive():
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/telegram-central/webhook",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "scheme": "http",
        },
        receive,
    )


class TelegramCentralIsolatedTests(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        shutil.rmtree(_TEST_DIR, ignore_errors=True)

    def setUp(self):
        self.db = Session()

    def tearDown(self):
        self.db.close()

    def create_user(self, role="user", active=True, status="active"):
        user = User(
            id=str(uuid.uuid4()),
            email=f"{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pengguna Tes",
            role=role,
            is_active=active,
            status=status,
        )
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def create_conversation(self, user):
        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=user.id,
            title="Percakapan pengujian",
        )
        self.db.add(conversation)
        self.db.commit()
        return conversation

    def test_01_link_code_stores_hash_not_plain_code(self):
        user = self.create_user()

        with patch.object(
            central, "OMAI_TELEGRAM_BOT_USERNAME", "OneMasterAI_Bot"
        ):
            result = central.create_central_link_code(user, self.db)

        link = self.db.query(TelegramCentralLink).filter_by(
            user_id=user.id
        ).one()

        code = result["link"].split("?start=", 1)[1]

        self.assertEqual(result["expires_in_seconds"], 900)
        self.assertNotEqual(link.link_code_hash, code)
        self.assertEqual(link.link_code_hash, central._hash_code(code))
        self.assertIsNotNone(link.link_code_expires_at)
        print("LULUS: kode pengaitan hanya disimpan dalam bentuk hash")

    def test_02_inactive_user_cannot_create_link_code(self):
        user = self.create_user(active=False)

        with self.assertRaises(HTTPException) as error:
            central.create_central_link_code(user, self.db)

        self.assertEqual(error.exception.status_code, 403)
        print("LULUS: akun nonaktif ditolak")

    def test_03_unlink_preserves_conversation_and_messages(self):
        user = self.create_user()
        conversation = self.create_conversation(user)

        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            skill="general",
            content="Riwayat yang harus dipertahankan",
        )
        link = TelegramCentralLink(
            user_id=user.id,
            telegram_user_id=123456789,
            telegram_username="pengguna_tes",
            conversation_id=conversation.id,
            link_code_hash="a" * 64,
            link_code_expires_at=datetime.utcnow() + timedelta(minutes=5),
            linked_at=datetime.utcnow(),
            is_active=True,
        )
        self.db.add_all([message, link])
        self.db.commit()

        result = central.unlink_central_telegram(user, self.db)

        self.db.refresh(link)
        self.assertFalse(link.is_active)
        self.assertIsNone(link.telegram_user_id)
        self.assertIsNone(link.telegram_username)
        self.assertIsNone(link.link_code_hash)
        self.assertEqual(link.conversation_id, conversation.id)

        saved_message = self.db.query(Message).filter_by(
            id=message.id
        ).one()
        self.assertEqual(
            saved_message.content,
            "Riwayat yang harus dipertahankan",
        )
        self.assertFalse(result["linked"])
        print("LULUS: unlink tidak menghapus percakapan atau pesan")

    async def test_04_webhook_rejects_wrong_secret(self):
        request = make_request({"update_id": 1001})

        with patch.object(
            central, "OMAI_TELEGRAM_WEBHOOK_SECRET", "secret-benar"
        ):
            with self.assertRaises(HTTPException) as error:
                await central.central_telegram_webhook(
                    request, "secret-salah", self.db
                )

        self.assertEqual(error.exception.status_code, 403)
        print("LULUS: webhook menolak secret yang salah")

    async def test_05_start_command_links_valid_account(self):
        user = self.create_user()
        code = "kode-pengujian-yang-tidak-dipakai-produksi"

        link = TelegramCentralLink(
            user_id=user.id,
            link_code_hash=central._hash_code(code),
            link_code_expires_at=datetime.utcnow() + timedelta(minutes=5),
            is_active=False,
        )
        self.db.add(link)
        self.db.commit()

        payload = {
            "update_id": 1002,
            "message": {
                "message_id": 55,
                "text": f"/start {code}",
                "from": {"id": 7654321, "username": "tester"},
                "chat": {"id": 7654321, "type": "private"},
            },
        }

        with (
            patch.object(
                central, "OMAI_TELEGRAM_WEBHOOK_SECRET", "secret-tes"
            ),
            patch.object(
                central, "_send_message", new_callable=AsyncMock
            ) as send_mock,
        ):
            result = await central.central_telegram_webhook(
                make_request(payload), "secret-tes", self.db
            )

        self.db.refresh(link)
        self.assertTrue(result["linked"])
        self.assertTrue(link.is_active)
        self.assertEqual(link.telegram_user_id, 7654321)
        self.assertEqual(link.telegram_username, "tester")
        self.assertIsNone(link.link_code_hash)
        send_mock.assert_awaited_once()
        print("LULUS: /start menghubungkan akun aktif")

    async def test_06_duplicate_processing_update_does_not_call_ai(self):
        user = self.create_user()

        existing = TelegramCentralUpdate(
            update_id=1003,
            telegram_user_id=7654321,
            telegram_message_id=56,
            user_id=user.id,
            status="processing",
        )
        self.db.add(existing)
        self.db.commit()

        payload = {
            "update_id": 1003,
            "message": {
                "message_id": 56,
                "text": "Jangan proses dua kali",
                "from": {"id": 7654321},
                "chat": {"id": 7654321, "type": "private"},
            },
        }

        with (
            patch.object(
                central, "OMAI_TELEGRAM_WEBHOOK_SECRET", "secret-tes"
            ),
            patch.object(central, "_chat_impl") as chat_mock,
        ):
            result = await central.central_telegram_webhook(
                make_request(payload), "secret-tes", self.db
            )

        self.assertTrue(result["duplicate"])
        self.assertEqual(result["status"], "processing")
        chat_mock.assert_not_called()
        print("LULUS: update processing duplikat tidak memanggil AI")

    async def test_07_duplicate_reply_pending_reuses_saved_reply(self):
        existing = TelegramCentralUpdate(
            update_id=1004,
            telegram_user_id=7654321,
            telegram_message_id=57,
            status="reply_pending",
        )
        self.db.add(existing)
        self.db.commit()

        payload = {
            "update_id": 1004,
            "message": {
                "message_id": 57,
                "text": "Pesan duplikat",
                "from": {"id": 7654321},
                "chat": {"id": 7654321, "type": "private"},
            },
        }

        with (
            patch.object(
                central, "OMAI_TELEGRAM_WEBHOOK_SECRET", "secret-tes"
            ),
            patch.object(
                central,
                "_deliver_saved_reply",
                new_callable=AsyncMock,
                return_value=True,
            ) as delivery_mock,
            patch.object(central, "_chat_impl") as chat_mock,
        ):
            result = await central.central_telegram_webhook(
                make_request(payload), "secret-tes", self.db
            )

        self.assertTrue(result["duplicate"])
        self.assertTrue(result["reply_retried"])
        delivery_mock.assert_awaited_once()
        chat_mock.assert_not_called()
        print("LULUS: reply_pending memakai jawaban tersimpan")

    async def test_08_normal_message_calls_chat_once_and_saves_reply(self):
        user = self.create_user()
        conversation = self.create_conversation(user)

        link = TelegramCentralLink(
            user_id=user.id,
            telegram_user_id=7654328,
            telegram_username="tester",
            conversation_id=conversation.id,
            linked_at=datetime.utcnow(),
            is_active=True,
        )

        assistant_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            skill="general",
            content="Jawaban simulasi",
        )

        reservation_id = str(uuid.uuid4())
        reservation = TokenReservation(
            id=reservation_id,
            user_id=user.id,
            subscription_id=None,
            reserved_topup=0,
            reserved_daily=100,
            actual_tokens=0,
            status="pending",
            reference="telegram-central:1005",
            description="Tes terisolasi",
        )

        self.db.add_all([link, assistant_message, reservation])
        self.db.commit()

        fake_result = ChatResponse(
            conversation_id=conversation.id,
            skill="general",
            answer="Jawaban simulasi",
            assistant_message_id=assistant_message.id,
            reservation_id=reservation_id,
        )

        payload = {
            "update_id": 1005,
            "message": {
                "message_id": 58,
                "text": "Uji pemanggilan AI",
                "from": {"id": 7654328},
                "chat": {"id": 7654328, "type": "private"},
            },
        }

        with (
            patch.object(
                central, "OMAI_TELEGRAM_WEBHOOK_SECRET", "secret-tes"
            ),
            patch.object(
                central,
                "_chat_impl",
                new_callable=Mock,
                return_value=fake_result,
            ) as chat_mock,
            patch.object(
                central,
                "_deliver_saved_reply",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            result = await central.central_telegram_webhook(
                make_request(payload), "secret-tes", self.db
            )

        update = self.db.query(TelegramCentralUpdate).filter_by(
            update_id=1005
        ).one()
        self.db.refresh(link)

        self.assertTrue(result["processed"])
        self.assertTrue(result["reply_sent"])
        chat_mock.assert_called_once()
        self.assertEqual(update.status, "reply_pending")
        self.assertEqual(update.user_id, user.id)
        self.assertEqual(update.assistant_message_id, assistant_message.id)
        self.assertEqual(update.reservation_id, reservation_id)
        self.assertEqual(link.conversation_id, conversation.id)
        print("LULUS: pesan normal memanggil alur chat satu kali")


    def create_saved_document_update(self, file_format, update_id):
        user = self.create_user()
        conversation = self.create_conversation(user)

        telegram_user_id = 8800000 + update_id
        link = TelegramCentralLink(
            user_id=user.id,
            telegram_user_id=telegram_user_id,
            telegram_username="document_tester",
            conversation_id=conversation.id,
            linked_at=datetime.utcnow(),
            is_active=True,
        )

        assistant_message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            skill="general",
            content="Isi dokumen pengujian ONE MASTER AI.\n"
                    "Baris kedua untuk memastikan isi tersimpan.",
        )

        reservation_id = str(uuid.uuid4())
        reservation = TokenReservation(
            id=reservation_id,
            user_id=user.id,
            subscription_id=None,
            reserved_topup=0,
            reserved_daily=100,
            actual_tokens=5,
            status="completed",
            reference=f"telegram-central:{update_id}",
            description="Pengujian dokumen Telegram",
            completed_at=datetime.utcnow(),
        )

        self.db.add_all([link, assistant_message, reservation])
        self.db.commit()

        stored_result = TokenReservationResult(
            reservation_id=reservation_id,
            user_id=user.id,
            assistant_message_id=assistant_message.id,
            response_content=assistant_message.content,
            skill="general",
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=5,
            status="settled",
            settled_at=datetime.utcnow(),
        )

        update = TelegramCentralUpdate(
            update_id=update_id,
            telegram_user_id=telegram_user_id,
            telegram_message_id=update_id,
            user_id=user.id,
            reservation_id=reservation_id,
            assistant_message_id=assistant_message.id,
            document_format=file_format,
            document_title="Dokumen Integrasi",
            document_content=(
                "# Laporan Pengujian\\n\\n"
                "Ini isi dokumen yang seharusnya masuk ke PDF atau DOCX."
            ),
            generated_file_id=uuid.uuid4().hex[:12],
            document_delivery_status="pending",
            status="reply_pending",
        )

        self.db.add_all([stored_result, update])
        self.db.commit()
        self.db.refresh(update)

        return user, update, assistant_message

    async def test_09_document_generation_and_delivery_is_idempotent(self):
        from app.services.document_service import STORAGE_DIR

        for index, file_format in enumerate(("docx", "pdf")):
            with self.subTest(file_format=file_format):
                user, update, assistant_message = (
                    self.create_saved_document_update(
                        file_format,
                        9100 + index,
                    )
                )

                with (
                    patch.object(
                        central,
                        "_send_message",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as message_mock,
                    patch.object(
                        central,
                        "send_generated_document",
                        new_callable=AsyncMock,
                        return_value=True,
                    ) as document_mock,
                ):
                    delivered = await central._deliver_saved_reply_locked(
                        self.db,
                        update,
                        update.telegram_user_id,
                    )

                    self.assertTrue(delivered)

                    self.db.refresh(update)
                    self.assertEqual(update.status, "completed")
                    self.assertEqual(
                        update.document_delivery_status,
                        "sent",
                    )
                    self.assertIsNotNone(update.response_sent_at)

                    generated_path = (
                        STORAGE_DIR
                        / "generated"
                        / user.id
                        / f"{update.generated_file_id}.{file_format}"
                    )
                    self.assertTrue(generated_path.is_file())
                    self.assertGreater(generated_path.stat().st_size, 0)

                    message_mock.assert_awaited_once()
                    document_mock.assert_awaited_once_with(
                        chat_id=update.telegram_user_id,
                        user_id=user.id,
                        file_id=update.generated_file_id,
                        file_format=file_format,
                        title="Dokumen Integrasi",
                    )

                    # Simulasi pemanggilan ulang setelah status completed.
                    duplicate_result = (
                        await central._deliver_saved_reply_locked(
                            self.db,
                            update,
                            update.telegram_user_id,
                        )
                    )

                    self.assertTrue(duplicate_result)
                    message_mock.assert_awaited_once()
                    document_mock.assert_awaited_once()

        print(
            "LULUS: DOCX dan PDF dibuat, dikirim, "
            "dan tidak dikirim ulang saat retry."
        )

    async def test_10_uncertain_document_delivery_is_not_retried(self):
        user, update, _ = self.create_saved_document_update(
            "pdf",
            9200,
        )

        # Simulasikan proses sebelumnya sudah memulai pengiriman,
        # tetapi hasil Telegram tidak diketahui.
        update.document_delivery_status = "sending"
        update.response_sent_at = datetime.utcnow()
        self.db.commit()

        with (
            patch.object(
                central,
                "_send_message",
                new_callable=AsyncMock,
                return_value=True,
            ) as message_mock,
            patch.object(
                central,
                "send_generated_document",
                new_callable=AsyncMock,
                return_value=True,
            ) as document_mock,
        ):
            delivered = await central._deliver_saved_reply_locked(
                self.db,
                update,
                update.telegram_user_id,
            )

        self.db.refresh(update)

        self.assertFalse(delivered)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code,
            "telegram_document_delivery_uncertain",
        )
        self.assertEqual(update.document_delivery_status, "sending")

        document_mock.assert_not_awaited()
        message_mock.assert_not_awaited()

        print(
            "LULUS: pengiriman tidak pasti masuk needs_review "
            "dan lampiran tidak dikirim ulang."
        )


    def create_filesaya_update(self, user):
        update = TelegramCentralUpdate(
            update_id=uuid.uuid4().int % 10**12,
            telegram_user_id=8800000 + (uuid.uuid4().int % 1000000),
            telegram_message_id=uuid.uuid4().int % 10**9,
            user_id=user.id,
            status="processing",
        )
        self.db.add(update)
        self.db.commit()
        self.db.refresh(update)
        return update

    async def test_11_filesaya_download_confirmed(self):
        user = self.create_user()
        update = self.create_filesaya_update(user)

        with (
            patch.object(
                central, "send_user_file",
                new_callable=AsyncMock, return_value=True,
            ) as file_mock,
            patch.object(
                central, "_send_message",
                new_callable=AsyncMock, return_value=True,
            ) as message_mock,
        ):
            handled = await central._handle_filesaya_command(
                "/unduh abc123", self.db, user,
                update.telegram_user_id, update,
            )

        self.db.refresh(update)
        self.assertTrue(handled)
        self.assertEqual(update.status, "completed")
        self.assertIsNone(update.error_code)
        file_mock.assert_awaited_once_with(
            update.telegram_user_id, user.id, "abc123"
        )
        message_mock.assert_awaited_once()
        print("LULUS: unduh terkonfirmasi dicatat completed.")

    async def test_12_filesaya_download_rejected(self):
        user = self.create_user()
        update = self.create_filesaya_update(user)

        with (
            patch.object(
                central, "send_user_file",
                new_callable=AsyncMock, return_value=False,
            ) as file_mock,
            patch.object(
                central, "_send_message",
                new_callable=AsyncMock, return_value=True,
            ) as message_mock,
        ):
            handled = await central._handle_filesaya_command(
                "/unduh abc123", self.db, user,
                update.telegram_user_id, update,
            )

        self.db.refresh(update)
        self.assertTrue(handled)
        self.assertEqual(update.status, "failed")
        self.assertEqual(
            update.error_code, "telegram_file_delivery_failed"
        )
        file_mock.assert_awaited_once()
        message_mock.assert_awaited_once()
        print("LULUS: penolakan unduh dicatat failed, bukan sukses.")

    async def test_13_filesaya_download_uncertain_not_retried(self):
        user = self.create_user()
        update = self.create_filesaya_update(user)

        with (
            patch.object(
                central, "send_user_file",
                new_callable=AsyncMock, return_value=None,
            ) as file_mock,
            patch.object(
                central, "_send_message",
                new_callable=AsyncMock,
            ) as message_mock,
        ):
            handled = await central._handle_filesaya_command(
                "/unduh abc123", self.db, user,
                update.telegram_user_id, update,
            )

        self.db.refresh(update)
        self.assertTrue(handled)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code, "telegram_file_delivery_uncertain"
        )
        self.assertIsNone(update.completed_at)
        file_mock.assert_awaited_once()
        message_mock.assert_not_awaited()
        print("LULUS: hasil unduh tidak pasti masuk needs_review tanpa kirim ulang.")

    async def test_14_filesaya_confirmation_uncertain_after_file_sent(self):
        user = self.create_user()
        update = self.create_filesaya_update(user)

        with (
            patch.object(
                central, "send_user_file",
                new_callable=AsyncMock, return_value=True,
            ) as file_mock,
            patch.object(
                central, "_send_message",
                new_callable=AsyncMock, return_value=None,
            ) as message_mock,
        ):
            handled = await central._handle_filesaya_command(
                "/unduh abc123", self.db, user,
                update.telegram_user_id, update,
            )

        self.db.refresh(update)
        self.assertTrue(handled)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code, "telegram_file_confirmation_uncertain"
        )
        file_mock.assert_awaited_once()
        message_mock.assert_awaited_once()
        print("LULUS: konfirmasi tidak pasti setelah file terkirim masuk needs_review.")

    async def test_15_filesaya_image_preview_uncertain(self):
        user = self.create_user()
        update = self.create_filesaya_update(user)

        with (
            patch.object(
                central, "get_user_file_extension",
                return_value=".png",
            ),
            patch.object(
                central, "send_user_image_preview",
                new_callable=AsyncMock, return_value=None,
            ) as image_mock,
            patch.object(
                central, "_send_message",
                new_callable=AsyncMock,
            ) as message_mock,
        ):
            handled = await central._handle_filesaya_command(
                "/baca abc123", self.db, user,
                update.telegram_user_id, update,
            )

        self.db.refresh(update)
        self.assertTrue(handled)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code, "telegram_file_delivery_uncertain"
        )
        image_mock.assert_awaited_once_with(
            update.telegram_user_id, user.id, "abc123"
        )
        message_mock.assert_not_awaited()
        print("LULUS: preview gambar tidak pasti masuk needs_review.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
