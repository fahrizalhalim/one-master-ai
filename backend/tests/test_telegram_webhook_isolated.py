import asyncio
import hashlib
import os
import tempfile
import uuid
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

fd, db_path = tempfile.mkstemp(
    prefix="one_master_telegram_webhook_",
    suffix=".db",
)
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.main import app
from app.models.database import Base, get_db
from app.models.user import User
from app.models.telegram_account import TelegramAccount
from app.models.telegram_update import TelegramUpdate
from app.models.conversation import Conversation, Message
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_usage import TokenUsage
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.api.routes_telegram import _deliver_saved_telegram_reply

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)

def override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()

class TelegramWebhookTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.previous_dependency_overrides = (
            app.dependency_overrides.copy()
        )
        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        app.dependency_overrides.update(
            cls.previous_dependency_overrides
        )
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

        self.db = Session()

        self.user = User(
            id=str(uuid.uuid4()),
            email=f"webhook-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pemilik Tes",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.code = "kode-linking-tes-123456"
        self.secret = "secret-webhook-test-123456"

        self.account = TelegramAccount(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            bot_id=920001,
            bot_username="bot_webhook_tes",
            bot_token_encrypted="encrypted-test-only",
            bot_token_hash=f"hash-{uuid.uuid4().hex}",
            webhook_secret=self.secret,
            link_code_hash=hashlib.sha256(
                self.code.encode()
            ).hexdigest(),
            link_code_expires_at=datetime.utcnow() + timedelta(minutes=10),
            status="approved",
        )
        self.db.add(self.account)
        self.db.commit()

        self.url = f"/api/v1/telegram/webhook/{self.account.id}"
        self.headers = {
            "X-Telegram-Bot-Api-Secret-Token": self.secret
        }

    def tearDown(self):
        self.db.close()

    def payload(self, update_id=1001, code=None):
        code = self.code if code is None else code
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "text": f"/start {code}",
                "from": {
                    "id": 700001,
                    "username": "pemilik_tes",
                },
                "chat": {
                    "id": 700001,
                    "type": "private",
                },
            },
        }

    def test_01_invalid_secret_is_rejected(self):
        response = self.client.post(
            self.url,
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret-salah"},
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.query(TelegramUpdate).count(), 0)
        print("LULUS: secret salah ditolak tanpa menyimpan update")

    def test_02_valid_link_activates_account_once(self):
        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-token",
        ), patch(
            "app.api.routes_telegram.send_telegram_message",
        ) as send_message:
            response = self.client.post(
                self.url,
                headers=self.headers,
                json=self.payload(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["linked"])

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account.id)

        self.assertEqual(saved.status, "active")
        self.assertEqual(saved.telegram_owner_id, 700001)
        self.assertEqual(saved.telegram_owner_username, "pemilik_tes")
        self.assertIsNone(saved.link_code_hash)
        self.assertIsNone(saved.link_code_expires_at)

        update = self.db.query(TelegramUpdate).one()
        self.assertEqual(update.status, "completed")
        self.assertIsNotNone(update.completed_at)

        send_message.assert_called_once()
        print("LULUS: linking valid mengaktifkan akun dan menghapus kode")

    def test_03_expired_code_does_not_link(self):
        self.account.link_code_expires_at = (
            datetime.utcnow() - timedelta(minutes=1)
        )
        self.db.commit()

        response = self.client.post(
            self.url,
            headers=self.headers,
            json=self.payload(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["linked"])

        self.db.expire_all()
        saved = self.db.get(TelegramAccount, self.account.id)

        self.assertEqual(saved.status, "approved")
        self.assertIsNone(saved.telegram_owner_id)
        print("LULUS: kode kedaluwarsa tidak mengaktifkan akun")

    def test_04_duplicate_update_is_not_processed_twice(self):
        with patch(
            "app.api.routes_telegram.decrypt_bot_token",
            return_value="fake-token",
        ), patch(
            "app.api.routes_telegram.send_telegram_message",
        ) as send_message:
            first = self.client.post(
                self.url,
                headers=self.headers,
                json=self.payload(update_id=2001),
            )
            second = self.client.post(
                self.url,
                headers=self.headers,
                json=self.payload(update_id=2001),
            )

        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["linked"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicate"])

        self.assertEqual(self.db.query(TelegramUpdate).count(), 1)
        send_message.assert_called_once()
        print("LULUS: update duplikat tidak diproses dua kali")

    def test_05_invalid_json_payload_is_rejected(self):
        response = self.client.post(
            self.url,
            headers={
                **self.headers,
                "Content-Type": "application/json",
            },
            content="bukan-json",
        )
        self.assertEqual(response.status_code, 400)
        print("LULUS: JSON tidak valid ditolak")


    def _make_saved_reply(
        self,
        *,
        result_status="received",
        reservation_status="pending",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    ):
        conversation = Conversation(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            title="Tes Telegram",
        )
        self.db.add(conversation)
        self.db.flush()

        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            skill="general",
            content="Jawaban tes",
        )
        self.db.add(message)

        reservation = TokenReservation(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            reserved_topup=100,
            reserved_daily=0,
            actual_tokens=0,
            status=reservation_status,
            reference="telegram-test",
            description="Reservasi test",
        )
        self.db.add(reservation)

        # Saldo 900 mewakili saldo setelah reservasi 100 token.
        wallet = TokenWallet(
            user_id=self.user.id,
            topup_balance=900,
        )
        self.db.add(wallet)

        update = TelegramUpdate(
            id=str(uuid.uuid4()),
            account_id=self.account.id,
            update_id=uuid.uuid4().int % 1000000000,
            reservation_id=reservation.id,
            assistant_message_id=message.id,
            status="reply_pending",
        )
        self.db.add(update)

        result = TokenReservationResult(
            reservation_id=reservation.id,
            user_id=self.user.id,
            assistant_message_id=message.id,
            response_content=message.content,
            skill="general",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status=result_status,
        )
        self.db.add(result)
        self.db.commit()

        return update, reservation, result, wallet

    def test_06_missing_token_usage_requires_review(self):
        update, reservation, result, wallet = self._make_saved_reply(
            prompt_tokens=None,
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(wallet)

        self.assertFalse(sent)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code,
            "reservation_token_usage_incomplete",
        )
        self.assertEqual(reservation.status, "pending")
        self.assertEqual(wallet.topup_balance, 900)
        self.assertEqual(self.db.query(TokenUsage).count(), 0)
        self.assertEqual(self.db.query(TokenTransaction).count(), 0)
        send_message.assert_not_called()

    def test_07_settled_result_with_pending_reservation_is_rejected(self):
        update, reservation, result, wallet = self._make_saved_reply(
            result_status="settled",
            reservation_status="pending",
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(wallet)

        self.assertFalse(sent)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code,
            "settled_reservation_not_completed",
        )
        self.assertEqual(reservation.status, "pending")
        self.assertEqual(wallet.topup_balance, 900)
        self.assertEqual(self.db.query(TokenUsage).count(), 0)
        self.assertEqual(self.db.query(TokenTransaction).count(), 0)
        send_message.assert_not_called()

    def test_08_received_result_settles_and_retry_is_idempotent(self):
        update, reservation, result, wallet = self._make_saved_reply()

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            send_message.return_value = True

            first = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

            second = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(result)
        self.db.refresh(wallet)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(update.status, "completed")
        self.assertEqual(reservation.status, "completed")
        self.assertEqual(reservation.actual_tokens, 15)
        self.assertEqual(result.status, "settled")
        self.assertEqual(wallet.topup_balance, 985)
        self.assertEqual(
            self.db.query(TokenUsage)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(TokenTransaction)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        send_message.assert_called_once()

    def test_09_zero_token_usage_is_valid(self):
        update, reservation, result, wallet = self._make_saved_reply(
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            send_message.return_value = True

            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(result)
        self.db.refresh(wallet)

        self.assertTrue(sent)
        self.assertEqual(update.status, "completed")
        self.assertEqual(reservation.status, "completed")
        self.assertEqual(reservation.actual_tokens, 0)
        self.assertEqual(result.status, "settled")
        self.assertEqual(wallet.topup_balance, 1000)
        self.assertEqual(
            self.db.query(TokenUsage)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(TokenTransaction)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        send_message.assert_called_once()


    def test_10_negative_token_usage_requires_review(self):
        update, reservation, result, wallet = self._make_saved_reply(
            prompt_tokens=-10,
            completion_tokens=25,
            total_tokens=15,
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(wallet)

        self.assertFalse(sent)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code,
            "reservation_token_usage_invalid",
        )
        self.assertEqual(reservation.status, "pending")
        self.assertEqual(wallet.topup_balance, 900)
        self.assertEqual(self.db.query(TokenUsage).count(), 0)
        self.assertEqual(self.db.query(TokenTransaction).count(), 0)
        send_message.assert_not_called()

    def test_11_mismatched_total_tokens_requires_review(self):
        update, reservation, result, wallet = self._make_saved_reply(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=20,
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(wallet)

        self.assertFalse(sent)
        self.assertEqual(update.status, "needs_review")
        self.assertEqual(
            update.error_code,
            "reservation_token_usage_mismatch",
        )
        self.assertEqual(reservation.status, "pending")
        self.assertEqual(wallet.topup_balance, 900)
        self.assertEqual(self.db.query(TokenUsage).count(), 0)
        self.assertEqual(self.db.query(TokenTransaction).count(), 0)
        send_message.assert_not_called()

    def test_12_valid_token_usage_still_settles(self):
        update, reservation, result, wallet = self._make_saved_reply(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

        with patch(
            "app.api.routes_telegram._send_telegram_message_checked",
            new_callable=AsyncMock,
        ) as send_message:
            send_message.return_value = True

            sent = asyncio.run(
                _deliver_saved_telegram_reply(
                    self.db, self.account, update, 700001, "fake-token"
                )
            )

        self.db.refresh(update)
        self.db.refresh(reservation)
        self.db.refresh(result)
        self.db.refresh(wallet)

        self.assertTrue(sent)
        self.assertEqual(update.status, "completed")
        self.assertEqual(reservation.status, "completed")
        self.assertEqual(reservation.actual_tokens, 15)
        self.assertEqual(result.status, "settled")
        self.assertEqual(wallet.topup_balance, 985)
        self.assertEqual(
            self.db.query(TokenUsage)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        self.assertEqual(
            self.db.query(TokenTransaction)
            .filter_by(reservation_id=reservation.id)
            .count(),
            1,
        )
        send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
