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


META = {"pass": 1, "run_id": "r", "model": "m", "prompt_version": "p1-abc", "updated_at": "t"}
EV = [{"url": "https://ex.com/docs", "quote": "The API uses OAuth 2.0 tokens"}]


@pytest.fixture
def make_row():
    from agent.schema import SCORED_FIELDS, AppResult

    def _make(**over) -> AppResult:
        d = dict(id=1, app="X", category="C", description="d", auth_methods=["oauth2"],
                 access_model="self_serve_free", api_type=["rest"], api_breadth="broad", existing_mcp="official",
                 verdict="buildable_now", blocker="none", api_pricing_tier="free", confidence=0.8,
                 evidence={f: EV for f in SCORED_FIELDS}, unknown_reason={}, flags=[], meta=META)
        d.update(over)
        return AppResult.model_validate(d)
    return _make
