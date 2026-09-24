import pytest

from agent.config import ConfigError, config_error_message, load_settings, secret_values

GOOD = {"OPENROUTER_API_KEY": "sk-or-v1-abc123", "COMPOSIO_API_KEY": "ck_x", "PASS1_MODEL": "a/b",
        "VERIFY_MODEL": "c/d", "JUDGE_MODEL": "e/f"}


def test_tests_cannot_see_real_env():  # Review Focus #2
    with pytest.raises(ConfigError):
        load_settings()


def test_loads_and_defaults_cap():
    s = load_settings(env=GOOD)
    assert s.budget_cap_usd == 4.0 and s.pass1_model == "a/b" and s.model_preset == "paid"


def test_reads_dotenv_file(tmp_path, monkeypatch):
    f = tmp_path / "x.env"
    f.write_text("\n".join(f"{k}={v}" for k, v in GOOD.items()) + "\nBUDGET_CAP_USD=2.5\n", encoding="utf-8")
    monkeypatch.setenv("TBR_DOTENV_PATH", str(f))
    s = load_settings()
    assert s.budget_cap_usd == 2.5 and s.openrouter_api_key.get_secret_value() == "sk-or-v1-abc123"


def test_process_env_overrides_dotenv(tmp_path, monkeypatch):
    f = tmp_path / "x.env"
    f.write_text("\n".join(f"{k}={v}" for k, v in GOOD.items()), encoding="utf-8")
    monkeypatch.setenv("TBR_DOTENV_PATH", str(f))
    monkeypatch.setenv("PASS1_MODEL", "override/model")
    assert load_settings().pass1_model == "override/model"


@pytest.mark.parametrize("var", ["OPENROUTER_API_KEY", "COMPOSIO_API_KEY", "PASS1_MODEL"])
def test_missing_var_names_variable_not_value(var):
    env = {k: v for k, v in GOOD.items() if k != var}
    with pytest.raises(ConfigError) as e:
        load_settings(env=env)
    msg = config_error_message(e.value)
    assert var in msg and ".env.example" in msg and "sk-or-v1-abc123" not in msg


def test_placeholder_rejected():
    with pytest.raises(ConfigError):
        load_settings(env=GOOD | {"OPENROUTER_API_KEY": "sk-or-v1-REPLACE_ME"})


@pytest.mark.parametrize("cap", ["lots", "-1"])
def test_bad_cap_rejected(cap):
    with pytest.raises(ConfigError) as e:
        load_settings(env=GOOD | {"BUDGET_CAP_USD": cap})
    assert e.value.var == "BUDGET_CAP_USD"


def test_repr_never_shows_key():
    s = load_settings(env=GOOD)
    assert "abc123" not in repr(s) and "abc123" not in str(s)
    assert "sk-or-v1-abc123" in secret_values(s)


def test_tools_optional_when_not_needed():
    env = {k: v for k, v in GOOD.items() if k != "COMPOSIO_API_KEY"}
    assert load_settings(env=env, need_tools=False).composio_api_key is None


def test_llm_optional_when_not_needed():
    env = {k: v for k, v in GOOD.items() if k not in ("OPENROUTER_API_KEY", "PASS1_MODEL")}
    assert load_settings(env=env, need_llm=False).openrouter_api_key is None
