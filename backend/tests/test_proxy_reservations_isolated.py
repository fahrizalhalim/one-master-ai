import asyncio
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

fd, db_path = tempfile.mkstemp(prefix="one_master_proxy_", suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from app.models.database import Base
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.models.subscription import TokenWallet, TokenTransaction
from app.models.token_reservation import TokenReservation
from app.models.token_reservation_result import TokenReservationResult
from app.models.token_usage import TokenUsage
from app.services.token_service import complete_reservation
from app.api import proxy

engine = create_engine(
    f"sqlite:///{db_path}",
    connect_args={"check_same_thread": False},
)
Session = sessionmaker(bind=engine, autoflush=False)


class FakeRequest:
    async def json(self):
        return {
            "messages": [{"role": "user", "content": "uji proxy"}],
            "max_tokens": 100,
        }


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.payload = payload if payload is not None else {
            "id": "chatcmpl-proxy-test",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "jawaban uji",
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        self.json_error = json_error
        self.text = "simulated Hermes response"
        self.request = httpx.Request("POST", proxy.HERMES_URL)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "simulated HTTP error",
                request=self.request,
                response=httpx.Response(
                    self.status_code,
                    request=self.request,
                    text=self.text,
                ),
            )

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeAsyncClient:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.response


class ProxyReservationTests(unittest.TestCase):
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
            email=f"proxy-{uuid.uuid4()}@example.invalid",
            password_hash="not-a-real-password",
            full_name="Proxy Test",
            role="user",
            status="active",
            is_active=True,
            api_key=f"proxy-test-{uuid.uuid4()}",
        )
        self.db.add(self.user)
        self.db.flush()

        self.db.add(TokenWallet(
            user_id=self.user.id,
            topup_balance=900,
        ))

        reservation = TokenReservation(
            id=str(uuid.uuid4()),
            user_id=self.user.id,
            subscription_id=None,
            reserved_topup=100,
            reserved_daily=0,
            actual_tokens=0,
            status="pending",
            reference="proxy-isolated-test",
            description="Reservasi proxy test",
        )
        self.db.add(reservation)
        self.db.commit()
        self.rid = reservation.id

    def tearDown(self):
        self.db.close()

    def balance(self):
        return self.db.query(TokenWallet).filter_by(
            user_id=self.user.id
        ).one().topup_balance

    def reservation_status(self):
        self.db.expire_all()
        return self.db.query(TokenReservation).filter_by(
            id=self.rid
        ).one().status

    def count(self, model, **filters):
        return self.db.query(model).filter_by(**filters).count()

    def invoke(self, response=None, error=None, settlement_error=None):
        fake_client = FakeAsyncClient(response=response, error=error)

        with (
            patch.object(proxy, "HERMES_API_KEY", "test-hermes-key"),
            patch.object(proxy, "get_authorized_user", return_value=self.user),
            patch.object(proxy, "reserve_tokens") as reserve_mock,
            patch.object(proxy.httpx, "AsyncClient", return_value=fake_client),
        ):
            reserve_mock.return_value = self.db.get(TokenReservation, self.rid)

            if settlement_error is not None:
                with patch.object(
                    proxy,
                    "complete_reservation",
                    side_effect=settlement_error,
                ):
                    return asyncio.run(proxy.chat_proxy(
                        FakeRequest(),
                        authorization="Bearer proxy-test-key",
                        db=self.db,
                    ))

            return asyncio.run(proxy.chat_proxy(
                FakeRequest(),
                authorization="Bearer proxy-test-key",
                db=self.db,
            ))

    def test_01_success_saves_evidence_then_settles_once(self):
        result = self.invoke()

        self.assertEqual(result["id"], "chatcmpl-proxy-test")
        self.assertEqual(self.reservation_status(), "completed")
        self.assertEqual(self.balance(), 985)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=self.rid), 1
        )
        self.assertEqual(
            self.count(
                TokenTransaction,
                reservation_id=self.rid,
                transaction_type="usage",
            ),
            1,
        )

        evidence = self.db.get(TokenReservationResult, self.rid)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.status, "settled")
        self.assertEqual(evidence.total_tokens, 15)
        self.assertIn("chatcmpl-proxy-test", evidence.response_content)

    def test_02_http_400_cancels_and_refunds(self):
        with self.assertRaises(HTTPException) as error:
            self.invoke(response=FakeResponse(status_code=400))

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.reservation_status(), "cancelled")
        self.assertEqual(self.balance(), 1000)
        self.assertEqual(
            self.count(TokenReservationResult, reservation_id=self.rid), 0
        )

    def test_03_http_429_holds_for_review(self):
        with self.assertRaises(HTTPException) as error:
            self.invoke(response=FakeResponse(status_code=429))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.reservation_status(), "needs_review")
        self.assertEqual(self.balance(), 900)

    def test_04_http_500_holds_for_review(self):
        with self.assertRaises(HTTPException) as error:
            self.invoke(response=FakeResponse(status_code=500))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.reservation_status(), "needs_review")
        self.assertEqual(self.balance(), 900)

    def test_05_connect_error_cancels_and_refunds(self):
        request = httpx.Request("POST", proxy.HERMES_URL)

        with self.assertRaises(HTTPException) as error:
            self.invoke(error=httpx.ConnectError(
                "connection refused",
                request=request,
            ))

        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(self.reservation_status(), "cancelled")
        self.assertEqual(self.balance(), 1000)

    def test_06_read_timeout_holds_for_review(self):
        request = httpx.Request("POST", proxy.HERMES_URL)

        with self.assertRaises(HTTPException) as error:
            self.invoke(error=httpx.ReadTimeout(
                "read timed out",
                request=request,
            ))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.reservation_status(), "needs_review")
        self.assertEqual(self.balance(), 900)

    def test_07_invalid_json_holds_for_review(self):
        with self.assertRaises(HTTPException) as error:
            self.invoke(response=FakeResponse(
                json_error=ValueError("invalid JSON")
            ))

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.reservation_status(), "needs_review")
        self.assertEqual(self.balance(), 900)

    def test_08_settlement_failure_keeps_evidence_for_admin(self):
        with self.assertRaises(HTTPException) as error:
            self.invoke(
                settlement_error=RuntimeError("simulated settlement failure")
            )

        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.reservation_status(), "pending")
        self.assertEqual(self.balance(), 900)

        evidence = self.db.get(TokenReservationResult, self.rid)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.status, "received")
        self.assertEqual(evidence.total_tokens, 15)

        # Simulasi pemulihan Admin menggunakan bukti yang tersimpan.
        complete_reservation(
            self.db,
            self.rid,
            actual_tokens=evidence.total_tokens,
            prompt_tokens=evidence.prompt_tokens,
            completion_tokens=evidence.completion_tokens,
            skill=evidence.skill,
            allow_needs_review=True,
        )

        self.assertEqual(self.reservation_status(), "completed")
        self.assertEqual(self.balance(), 985)
        self.assertEqual(
            self.count(TokenUsage, reservation_id=self.rid), 1
        )


if __name__ == "__main__":
    unittest.main()
