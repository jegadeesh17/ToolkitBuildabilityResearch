import socket

import pytest


@pytest.fixture(autouse=True)
def _offline(monkeypatch, request, tmp_path):
    """No keys, no real .env, no sockets: tests can never make a paid call."""
    for var in ("OPENROUTER_API_KEY", "COMPOSIO_API_KEY", "PASS1_MODEL", "VERIFY_MODEL",
                "JUDGE_MODEL", "BUDGET_CAP_USD", "MODEL_PRESET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("TBR_DOTENV_PATH", str(tmp_path / "no.env"))
    if "browser" not in request.keywords:
        def _blocked(*args, **kwargs):
            raise RuntimeError("network access attempted in an offline test")
        monkeypatch.setattr(socket.socket, "connect", _blocked)
