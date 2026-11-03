"""Tes: /docs & /openapi.json tertutup secara default di produksi."""
import importlib
import os
import sys


def _build_app(docs_public: str):
    """Buat app segar dengan env API_DOCS_PUBLIC tertentu."""
    for mod in list(sys.modules):
        if mod.startswith("app."):
            del sys.modules[mod]
    os.environ["OPENAI_API_KEY"] = "test-key-dummy"
    os.environ["DATABASE_URL"] = "sqlite:///./test_docs.db"
    os.environ["API_DOCS_PUBLIC"] = docs_public

    import app.main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def test_docs_closed_by_default():
    app = _build_app("false")
    c = TestClientCls = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
    client = c(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404
    # endpoint inti tetap jalan
    assert client.get("/health").status_code == 200


def test_docs_open_when_enabled():
    app = _build_app("true")
    c = TestClientCls = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient
    client = c(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
