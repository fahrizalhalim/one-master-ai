import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Gunakan database sementara sebelum mengimpor aplikasi dan model.
fd, db_path = tempfile.mkstemp(
    prefix="one_master_admin_settlement_",
    suffix=".db",
)
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.main import app
from app.api.dependencies import get_current_user
from app.models.database import Base, get_db
from app.models.user import User
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.token_usage import TokenUsage

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


def fake_verify_admin_password(admin, password):
    if password != "correct":
        raise HTTPException(
            status_code=401,
            detail="Password admin salah.",
        )


class AdminSettlementTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.previous_dependency_overrides = (
            app.dependency_overrides.copy()
        )
        app.dependency_overrides[get_current_user] = override_current_user
        app.dependency_overrides[get_db] = override_get_db

        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        cls.password_patch = patch(
            "app.api.routes_admin.verify_admin_password",
            side_effect=fake_verify_admin_password,
        )
        cls.password_patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.password_patch.stop()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(
            cls.previous_dependency_overrides
        )
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
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
        self.user = User(
            id=str(uuid.uuid4()),
            email=f"user-{uuid.uuid4()}@example.invalid",
            password_hash="test-only",
            full_name="Pengguna Tes",
            role="user",
            status="active",
            is_active=True,
        )

        self.db.add_all([self.admin, self.user])
        self.db.flush()

        # Saldo 900 menyimulasikan reservasi 100 token yang sudah dipotong.
        self.db.add(TokenWallet(
            user_id=self.user.id,
            topup_balance=900,
        ))
        self.db.commit()

        identity["user"] = self.admin

    def tearDown(self):
        self.db.close()

    def create_case(
        self,
        reservation_status="pending",
        result_status="received",
        prompt_tokens=40,
        completion_tokens=20,
        total_tokens=60,
    ):
        rid = str(uuid.uuid4())

        reservation = TokenReservation(
            id=rid,
            user_id=self.user.id,
            subscription_id=None,
            reserved_topup=100,
            reserved_daily=0,
            actual_tokens=0,
            status=reservation_status,
            reference="admin-settlement-test",
            description="Tes settlement Admin",
        )

        result = TokenReservationResult(
            reservation_id=rid,
            user_id=self.user.id,
            assistant_message_id=None,
            response_content="Jawaban AI pengujian",
            skill="general",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            status=result_status,
        )

        self.db.add_all([reservation, result])
        self.db.commit()
        return rid

    def wallet_balance(self):
        self.db.expire_all()
        return self.db.get(TokenWallet, self.user.id).topup_balance

    def count(self, model, **filters):
        self.db.expire_all()
        return self.db.query(model).filter_by(**filters).count()

    def settle(self, rid, password="correct"):
        return self.client.post(
            f"/api/v1/admin/reservations/{rid}/settle",
            json={"admin_password": password},
        )

    def cancel(self, rid, password="correct"):
        return self.client.post(
            f"/api/v1/admin/reservations/{rid}/cancel",
            json={"admin_password": password},
        )

    def create_reservation_without_result(
        self,
        reservation_status="needs_review",
    ):
        rid = str(uuid.uuid4())

        reservation = TokenReservation(
            id=rid,
            user_id=self.user.id,
            subscription_id=None,
            reserved_topup=100,
            reserved_daily=0,
            actual_tokens=0,
            status=reservation_status,
            reference="admin-cancel-test",
            description="Tes pembatalan Admin",
        )

        self.db.add(reservation)
        self.db.commit()
        return rid

    def test_01_pending_reservation_settles(self):
        rid = self.create_case()

        response = self.settle(rid)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result_status"], "settled")
        self.assertEqual(self.wallet_balance(), 940)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            1,
        )
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="usage",
            ),
            1,
        )
        print("LULUS: reservasi pending berhasil diselesaikan Admin")

    def test_02_needs_review_reservation_settles(self):
        rid = self.create_case(
            reservation_status="needs_review",
            result_status="needs_review",
        )

        response = self.settle(rid)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wallet_balance(), 940)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            1,
        )
        print("LULUS: reservasi needs_review dapat diselesaikan Admin")

    def test_03_repeated_settlement_is_idempotent(self):
        rid = self.create_case()

        first = self.settle(rid)
        self.assertEqual(first.status_code, 200, first.text)
        balance_after_first = self.wallet_balance()

        second = self.settle(rid)

        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(self.wallet_balance(), balance_after_first)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            1,
        )
        self.assertEqual(
            self.count(TokenTransaction, reservation_id=rid),
            1,
        )
        print("LULUS: settlement ulang tidak menggandakan saldo/transaksi")

    def test_04_wrong_admin_password_rejected(self):
        rid = self.create_case()

        response = self.settle(rid, password="wrong")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            0,
        )
        print("LULUS: password Admin salah ditolak")

    def test_05_inconsistent_token_total_rejected(self):
        rid = self.create_case(total_tokens=61)

        response = self.settle(rid)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            0,
        )
        print("LULUS: total token yang tidak cocok ditolak")

    def test_06_integer_overflow_rejected(self):
        rid = self.create_case(
            prompt_tokens=2_147_483_648,
            completion_tokens=0,
            total_tokens=2_147_483_648,
        )

        response = self.settle(rid)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=rid),
            0,
        )
        print("LULUS: token melebihi batas INTEGER ditolak")


    def test_07_admin_cancels_needs_review_and_refunds(self):
        rid = self.create_reservation_without_result()

        response = self.cancel(rid)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["reservation_status"], "cancelled")
        self.assertEqual(self.wallet_balance(), 1000)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="refund",
            ),
            1,
        )

        self.db.expire_all()
        reservation = self.db.get(TokenReservation, rid)
        self.assertEqual(reservation.status, "cancelled")
        print("LULUS: Admin membatalkan needs_review dan refund dikembalikan")

    def test_08_repeated_admin_cancel_does_not_refund_twice(self):
        rid = self.create_reservation_without_result()

        first = self.cancel(rid)
        self.assertEqual(first.status_code, 200, first.text)
        balance_after_first = self.wallet_balance()

        second = self.cancel(rid)

        self.assertEqual(second.status_code, 409)
        self.assertEqual(self.wallet_balance(), balance_after_first)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="refund",
            ),
            1,
        )
        print("LULUS: pembatalan ulang tidak menggandakan refund")

    def test_09_admin_cancel_rejected_when_ai_result_exists(self):
        rid = self.create_case(
            reservation_status="needs_review",
            result_status="needs_review",
            prompt_tokens=None,
            completion_tokens=None,
            total_tokens=None,
        )

        response = self.cancel(rid)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="refund",
            ),
            0,
        )

        self.db.expire_all()
        reservation = self.db.get(TokenReservation, rid)
        self.assertEqual(reservation.status, "needs_review")
        print("LULUS: bukti hasil AI mencegah pembatalan dan refund")

    def test_10_admin_cancel_wrong_password_rejected(self):
        rid = self.create_reservation_without_result()

        response = self.cancel(rid, password="wrong")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="refund",
            ),
            0,
        )
        print("LULUS: password Admin salah menolak pembatalan")

    def test_11_admin_cannot_cancel_completed_reservation(self):
        rid = self.create_case(
            reservation_status="completed",
            result_status="settled",
        )

        response = self.cancel(rid)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.wallet_balance(), 900)
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=rid,
                transaction_type="refund",
            ),
            0,
        )
        print("LULUS: reservasi completed tidak dapat dibatalkan")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)
