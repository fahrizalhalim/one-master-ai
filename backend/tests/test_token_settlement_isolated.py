import os
import tempfile
import uuid
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

# DATABASE_URL harus ditentukan sebelum import model.
fd, db_path = tempfile.mkstemp(prefix="one_master_token_", suffix=".db")
os.close(fd)

os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.models.database import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.token_usage import TokenUsage
from app.services.token_service import (
    complete_reservation,
    cancel_reservation,
)

# Import seluruh model yang dibutuhkan sebelum create_all.
engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)


class TokenSettlementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
        self.db = Session()
        self.user = User(
            id=str(uuid.uuid4()),
            email=f"token-test-{uuid.uuid4()}@example.invalid",
            password_hash="not-a-real-password",
            full_name="User Pengujian",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.db.add(TokenWallet(
            user_id=self.user.id,
            topup_balance=1000,
        ))
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def create_reservation(self, amount=100):
        reservation = TokenReservation(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            subscription_id=None,
            reserved_topup=amount,
            reserved_daily=0,
            actual_tokens=0,
            status="pending",
            reference="isolated-test",
            description="Pengujian terisolasi",
        )
        self.db.add(reservation)
        self.db.commit()
        return reservation.id

    def wallet_balance(self):
        return self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one().topup_balance

    def count(self, model, **filters):
        return self.db.query(model).filter_by(**filters).count()

    def test_01_first_settlement(self):
        rid = self.create_reservation(100)

        complete_reservation(
            self.db, rid,
            actual_tokens=60,
            prompt_tokens=40,
            completion_tokens=20,
            skill="general",
        )

        self.assertEqual(self.wallet_balance(), 1040)
        self.assertEqual(self.count(TokenUsage, reservation_id=rid), 1)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="usage",
            ),
            1,
        )
        print("LULUS: settlement pertama")

    def test_02_identical_retry(self):
        rid = self.create_reservation(100)

        args = dict(
            actual_tokens=60,
            prompt_tokens=40,
            completion_tokens=20,
            skill="general",
        )

        complete_reservation(self.db, rid, **args)
        balance_before = self.wallet_balance()

        complete_reservation(self.db, rid, **args)

        self.assertEqual(self.wallet_balance(), balance_before)
        self.assertEqual(self.count(TokenUsage, reservation_id=rid), 1)
        self.assertEqual(
            self.count(TokenTransaction, reservation_id=rid),
            1,
        )
        print("LULUS: retry identik tidak menggandakan pemotongan")

    def test_03_different_retry_rejected(self):
        rid = self.create_reservation(100)

        complete_reservation(
            self.db, rid, 60, 40, 20
        )
        balance_before = self.wallet_balance()

        with self.assertRaises(HTTPException) as error:
            complete_reservation(
                self.db, rid, 70, 45, 25
            )

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.wallet_balance(), balance_before)
        self.assertEqual(self.count(TokenUsage, reservation_id=rid), 1)
        print("LULUS: retry berbeda ditolak HTTP 409")

    def test_04_cancel_and_refund(self):
        rid = self.create_reservation(100)

        # Simulasikan saldo sudah dipotong ketika reservasi dibuat.
        wallet = self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one()
        wallet.topup_balance = 900
        self.db.commit()

        cancel_reservation(self.db, rid)

        self.assertEqual(self.wallet_balance(), 1000)
        reservation = self.db.query(TokenReservation).filter_by(
            id=rid
        ).one()
        self.assertEqual(reservation.status, "cancelled")
        self.assertEqual(
            self.count(
                TokenTransaction,
                reference="isolated-test",
                transaction_type="refund",
            ),
            1,
        )

        # Retry cancel tidak boleh membuat refund kedua.
        cancel_reservation(self.db, rid)
        self.assertEqual(self.wallet_balance(), 1000)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reference="isolated-test",
                transaction_type="refund",
            ),
            1,
        )
        print("LULUS: cancel dan refund idempotent")

    def test_05_cancelled_reservation_cannot_settle(self):
        rid = self.create_reservation(100)

        reservation = self.db.query(TokenReservation).filter_by(
            id=rid
        ).one()
        reservation.status = "cancelled"
        self.db.commit()

        with self.assertRaises(HTTPException) as error:
            complete_reservation(
                self.db, rid, 50, 30, 20
            )

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.count(TokenUsage, reservation_id=rid), 0)
        print("LULUS: reservasi cancelled tidak dapat diselesaikan")

    def test_06_extra_usage_creates_negative_balance(self):
        rid = self.create_reservation(100)

        # Simulasikan pemotongan saldo saat reservasi dibuat.
        wallet = self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one()
        wallet.topup_balance -= 100
        self.db.commit()

        self.assertEqual(self.wallet_balance(), 900)

        complete_reservation(
            self.db, rid,
            actual_tokens=120,
            prompt_tokens=80,
            completion_tokens=40,
        )

        self.assertEqual(self.wallet_balance(), 880)
        print("LULUS: pemakaian melebihi reservasi dicatat")

    def test_07_zero_actual_tokens(self):
        rid = self.create_reservation(100)

        complete_reservation(
            self.db, rid,
            actual_tokens=0,
            prompt_tokens=0,
            completion_tokens=0,
        )

        self.assertEqual(self.wallet_balance(), 1100)
        self.assertEqual(self.count(TokenUsage, reservation_id=rid), 1)
        print("LULUS: settlement nol token")

    def test_08_settlement_without_proof_rejected(self):
        rid = self.create_reservation(100)

        complete_reservation(
            self.db, rid, 60, 40, 20
        )

        # Simulasikan bukti transaksi hilang.
        self.db.query(TokenUsage).filter_by(
            reservation_id=rid
        ).delete()
        self.db.commit()

        balance_before = self.wallet_balance()

        with self.assertRaises(HTTPException) as error:
            complete_reservation(
                self.db, rid, 60, 40, 20
            )

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.wallet_balance(), balance_before)
        print("LULUS: settlement tanpa bukti ditolak")

    def test_10_cancel_blocked_when_ai_result_exists(self):
        rid = self.create_reservation(100)

        wallet = self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one()
        wallet.topup_balance = 900

        self.db.add(TokenReservationResult(
            reservation_id=rid,
            user_id=self.user.id,
            assistant_message_id=None,
            response_content="Hasil AI pengujian",
            skill="general",
            prompt_tokens=40,
            completion_tokens=20,
            total_tokens=60,
            status="received",
        ))
        self.db.commit()

        balance_before = self.wallet_balance()
        refund_count_before = self.count(
            TokenTransaction,
            reference="isolated-test",
            transaction_type="refund",
        )

        with self.assertRaises(HTTPException) as error:
            cancel_reservation(self.db, rid)

        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.wallet_balance(), balance_before)

        reservation = self.db.query(TokenReservation).filter_by(
            id=rid
        ).one()
        self.assertEqual(reservation.status, "pending")

        self.assertEqual(
            self.count(
                TokenTransaction,
                reference="isolated-test",
                transaction_type="refund",
            ),
            refund_count_before,
        )
        print("LULUS: hasil AI tersimpan tidak dapat direfund")

    def test_09_user_data_isolated(self):
        self.assertEqual(
            self.db.query(User).filter_by(
                id=self.user.id
            ).count(),
            1,
        )
        print("LULUS: user hanya berada di SQLite sementara")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)
