from unittest.mock import patch
import unittest

from app.services.master_agent import ask_ai, ProviderCallError


class ProviderFallbackTests(unittest.TestCase):

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_01_groq_success_no_fallback(self, groq, legacy):
        groq.return_value = {
            "text": "Jawaban Groq",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        }

        result = ask_ai("Tes", "groq-gpt-oss-120b")

        self.assertEqual(result["text"], "Jawaban Groq")
        groq.assert_called_once()
        legacy.assert_not_called()

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_02_groq_definitive_failure_uses_legacy(self, groq, legacy):
        groq.side_effect = ProviderCallError(
            "Groq menolak",
            safe_to_refund=True,
            reason="groq_rejected",
        )
        legacy.return_value = {
            "text": "Jawaban Ai.sumopod",
            "prompt_tokens": 120,
            "completion_tokens": 60,
            "provider": "aisumopod",
            "model": "legacy-model",
            "provider_route": "fallback",
        }

        result = ask_ai("Tes", "groq-gpt-oss-120b")

        self.assertEqual(result["text"], "Jawaban Ai.sumopod")
        self.assertEqual(result["provider"], "aisumopod")
        self.assertEqual(result["model"], "legacy-model")
        self.assertEqual(result["provider_route"], "fallback")
        self.assertEqual(result["prompt_tokens"], 120)
        self.assertEqual(result["completion_tokens"], 60)
        self.assertEqual(
            result["prompt_tokens"] + result["completion_tokens"],
            180,
        )
        groq.assert_called_once()
        legacy.assert_called_once()

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_03_uncertain_groq_failure_does_not_fallback(self, groq, legacy):
        groq.side_effect = ProviderCallError(
            "Timeout",
            safe_to_refund=False,
            reason="groq_timeout",
        )

        with self.assertRaises(ProviderCallError) as context:
            ask_ai("Tes", "groq-gpt-oss-120b")

        self.assertFalse(context.exception.safe_to_refund)
        legacy.assert_not_called()

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_04_both_definitive_failures_are_refundable(self, groq, legacy):
        groq.side_effect = ProviderCallError(
            "Groq ditolak",
            safe_to_refund=True,
            reason="groq_rejected",
        )
        legacy.side_effect = ProviderCallError(
            "Legacy ditolak",
            safe_to_refund=True,
            reason="legacy_rejected",
        )

        with self.assertRaises(ProviderCallError) as context:
            ask_ai("Tes", "groq-gpt-oss-120b")

        self.assertTrue(context.exception.safe_to_refund)
        groq.assert_called_once()
        legacy.assert_called_once()

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_05_legacy_uncertain_failure_is_not_refundable(self, groq, legacy):
        groq.side_effect = ProviderCallError(
            "Groq ditolak",
            safe_to_refund=True,
            reason="groq_rejected",
        )
        legacy.side_effect = ProviderCallError(
            "Legacy timeout",
            safe_to_refund=False,
            reason="legacy_timeout",
        )

        with self.assertRaises(ProviderCallError) as context:
            ask_ai("Tes", "groq-gpt-oss-120b")

        self.assertFalse(context.exception.safe_to_refund)

    @patch("app.services.master_agent._ask_legacy")
    @patch("app.services.master_agent._ask_groq")
    def test_06_manual_legacy_does_not_call_groq(self, groq, legacy):
        legacy.return_value = {
            "text": "Jawaban manual",
            "prompt_tokens": 10,
            "completion_tokens": 5,
        }

        result = ask_ai("Tes", "default")

        self.assertEqual(result["text"], "Jawaban manual")
        legacy.assert_called_once()
        groq.assert_not_called()


if __name__ == "__main__":
    unittest.main()
