"""Tes keamanan dokumentasi API dalam proses Python terisolasi."""

import os
import subprocess
import sys
import textwrap


def _check_docs(docs_public: str):
    script = textwrap.dedent(
        """
        import os
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        expected = os.environ["API_DOCS_PUBLIC"].lower() == "true"

        if expected:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
            assert client.get("/redoc").status_code == 200
        else:
            assert client.get("/docs").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            assert client.get("/redoc").status_code == 404

        assert client.get("/health").status_code == 200
        """
    )

    env = os.environ.copy()
    env["API_DOCS_PUBLIC"] = docs_public
    env["OPENAI_API_KEY"] = "test-key-dummy"

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Subprocess gagal untuk API_DOCS_PUBLIC={docs_public}\\n"
        f"STDOUT:\\n{result.stdout}\\n"
        f"STDERR:\\n{result.stderr}"
    )


def test_docs_closed_by_default():
    _check_docs("false")


def test_docs_open_when_enabled():
    _check_docs("true")
