import os
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# Database SQLite sementara harus ditentukan sebelum import aplikasi.
fd, db_path = tempfile.mkstemp(prefix="one_master_chat_e2e_", suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from app.models.database import Base, get_db
from app.models.user import User
from app.models.conversation import Conversation, Message
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.token_usage import TokenUsage
from app.api.dependencies import get_current_user
from app.services.master_agent import ProviderCallError
from app.main import app

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)


class ChatFallbackE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)

    @classmethod
    def tearDownClass(cls):
        # Jangan menghapus dependency override milik test suite lain.
        engine.dispose()
        Path(db_path).unlink(missing_ok=True)

    def setUp(self):
        # Simpan override sebelumnya agar tidak mengganggu test lain.
        self._previous_dependency_overrides = (
            app.dependency_overrides.copy()
        )
        self.db = Session()
        self.user = User(
            id=str(uuid.uuid4()),
            email=f"chat-e2e-{uuid.uuid4()}@example.invalid",
            password_hash="not-a-real-password",
            full_name="Pengguna E2E",
            profession="umum",
            role="user",
            status="active",
            is_active=True,
        )
        self.db.add(self.user)
        self.db.flush()

        self.wallet = TokenWallet(
            user_id=self.user.id,
            topup_balance=10000,
        )
        self.db.add(self.wallet)
        self.db.commit()

        def override_db():
            yield self.db

        def override_user():
            return self.user

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(
            self._previous_dependency_overrides
        )
        self.db.close()

    def test_groq_definitive_failure_falls_back_and_settles_once(self):
        # Jalankan master_agent dan ask_ai yang asli.
        # Hanya dua provider eksternal yang disimulasikan.
        fallback_result = {
            "text": "Jawaban dari Ai.sumopod melalui fallback.",
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "provider": "aisumopod",
            "model": "legacy-model",
            "provider_route": "fallback",
        }

        with patch(
            "app.services.master_agent._ask_groq",
            side_effect=ProviderCallError(
                "Groq menolak permintaan",
                safe_to_refund=True,
                reason="groq_rejected",
            ),
        ) as mocked_groq, patch(
            "app.services.master_agent._ask_legacy",
            return_value=fallback_result,
        ) as mocked_legacy:
            response = self.client.post(
                "/api/v1/chat",
                json={
                    "message": "Uji fallback Groq ke Ai.sumopod",
                    "model_id": "groq-gpt-oss-120b",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["answer"],
            fallback_result["text"],
        )

        # Pastikan keputusan fallback benar-benar dijalankan.
        mocked_groq.assert_called_once()
        mocked_legacy.assert_called_once_with(
            unittest.mock.ANY,
            provider_route="fallback",
        )

        reservations = self.db.query(TokenReservation).filter_by(
            user_id=self.user.id
        ).all()
        self.assertEqual(len(reservations), 1)

        reservation = reservations[0]
        self.assertEqual(reservation.status, "completed")
        self.assertEqual(reservation.actual_tokens, 180)

        usages = self.db.query(TokenUsage).filter_by(
            reservation_id=reservation.id
        ).all()
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0].total_tokens, 180)
        self.assertEqual(usages[0].prompt_tokens, 120)
        self.assertEqual(usages[0].completion_tokens, 60)

        transactions = self.db.query(TokenTransaction).filter_by(
            reservation_id=reservation.id,
            transaction_type="usage",
        ).all()

        print("transaction_amounts:", [t.amount for t in transactions])
        print("==========================================\\n")
        self.assertEqual(len(transactions), 1)
        # Seluruh pemakaian berasal dari kuota paket harian.
        # Tidak ada pemotongan saldo Top Up.
        self.assertEqual(transactions[0].amount, 0)

        stored_result = self.db.get(
            TokenReservationResult,
            reservation.id,
        )
        self.assertIsNotNone(stored_result)
        self.assertEqual(stored_result.status, "settled")
        self.assertEqual(stored_result.total_tokens, 180)

        # Kuota harian mencukupi, sehingga saldo Top Up tetap utuh.
        wallet = self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one()
        self.assertEqual(wallet.topup_balance, 10000)

        self.assertEqual(
            self.db.query(Message).filter_by(
                conversation_id=response.json()["conversation_id"],
                role="assistant",
            ).count(),
            1,
        )

        print("LULUS: endpoint chat menyimpan satu hasil dan satu settlement.")


if __name__ == "__main__":
    unittest.main()
