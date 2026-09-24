import json

import pytest

import agent.run as run_mod
from agent.observability import RunLogger
from agent.run import main
from tests.conftest import TEST_ENV, FakeLLM, FakeTools
from tests.test_pipeline import DOC, PAGE_TEXT, VALID


@pytest.fixture
def fake_env(monkeypatch):
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)


@pytest.fixture
def seed_cost():
    import agent.config as config

    def _seed(cost):
        RunLogger(config.RUN_LOG).append({"kind": "llm", "cost_usd": cost})
    return _seed


@pytest.fixture
def fake_run(monkeypatch):
    real = run_mod.research_app

    def _make(fail_ids=()):
        failing = set(fail_ids)

        async def research(app, **kw):
            if app.id in failing:
                raise RuntimeError("boom")
            return await real(app, **kw)
        monkeypatch.setattr(run_mod, "research_app", research)

        def factory(settings, logger, budget, run_id):
            factory.llm = FakeLLM(default=VALID, budget=budget)
            factory.tools = FakeTools({DOC: PAGE_TEXT}, [DOC])
            return factory.llm, factory.tools
        factory.llm = factory.tools = None
        return factory
    return _make


def rows(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_unknown_app_exit_2_lists_apps(capsys):
    assert main(["--app", "NoSuchApp"]) == 2
    out = capsys.readouterr().out
    assert "Salesforce" in out and "NoSuchApp" in out


def test_app_name_is_case_insensitive(fake_env, fake_run, tmp_path):
    out = tmp_path / "p.json"
    assert main(["--app", "pipedrive", "--out", str(out)], clients_factory=fake_run()) == 0
    assert [r["app"] for r in rows(out)] == ["Pipedrive"]


@pytest.mark.parametrize("ids", ["0,101", "abc", "3,,x"])
def test_bad_ids_exit_2(ids):
    assert main(["--ids", ids]) == 2


def test_no_selector_exit_2():
    assert main([]) == 2


def test_two_selectors_exit_2():
    assert main(["--all", "--pilot"]) == 2


def test_missing_config_exit_3(capsys):
    assert main(["--ids", "3"]) == 3
    out = capsys.readouterr().out
    assert ".env.example" in out and "OPENROUTER_API_KEY" in out


def test_budget_exceeded_exit_4_no_calls(tmp_path, fake_env, fake_run, seed_cost, capsys):
    seed_cost(5.0)  # default cap 4.00 already spent
    factory = fake_run()
    assert main(["--ids", "3", "--out", str(tmp_path / "p.json")], clients_factory=factory) == 4
    assert factory.llm.calls == 0 and factory.tools.calls == 0
    assert "budget" in capsys.readouterr().out.lower()


def test_budget_stop_keeps_partial_output_and_resume_finishes(tmp_path, fake_env, fake_run, monkeypatch):
    # Review Focus #4. Each fake call costs 0.001 and checks/records the budget like the real client;
    # after app 1, spent == cap, so the next app's pre-start check refuses.
    monkeypatch.setenv("BUDGET_CAP_USD", "0.001")
    out = tmp_path / "p.json"
    assert main(["--ids", "3,4,6", "--out", str(out), "--concurrency", "1"], clients_factory=fake_run()) == 4
    assert len(rows(out)) == 1
    monkeypatch.setenv("BUDGET_CAP_USD", "4.00")
    f2 = fake_run()
    assert main(["--ids", "3,4,6", "--out", str(out), "--resume"], clients_factory=f2) == 0
    assert sorted(r["id"] for r in rows(out)) == [3, 4, 6] and f2.llm.calls == 2


def test_errored_app_left_out_and_retried_on_resume(tmp_path, fake_env, fake_run):
    out = tmp_path / "p.json"
    assert main(["--ids", "3,4", "--out", str(out)], clients_factory=fake_run(fail_ids={4})) == 1
    assert [r["id"] for r in rows(out)] == [3]
    assert main(["--ids", "3,4", "--out", str(out), "--resume"], clients_factory=fake_run()) == 0
    assert [r["id"] for r in rows(out)] == [3, 4]


def test_app_error_is_logged(tmp_path, fake_env, fake_run):
    import agent.config as config
    from agent.observability import read_log
    main(["--ids", "4", "--out", str(tmp_path / "p.json")], clients_factory=fake_run(fail_ids={4}))
    errs = [r for r in read_log(config.RUN_LOG) if r["kind"] == "app_error"]
    assert errs and errs[0]["app_id"] == 4 and "RuntimeError" in errs[0]["error"]


def test_rerun_without_resume_replaces_only_selected(tmp_path, fake_env, fake_run):  # D11
    out = tmp_path / "p.json"
    main(["--ids", "3,4", "--out", str(out)], clients_factory=fake_run())
    before = {r["id"]: r["meta"]["run_id"] for r in rows(out)}
    main(["--ids", "4", "--out", str(out)], clients_factory=fake_run())
    after = {r["id"]: r["meta"]["run_id"] for r in rows(out)}
    assert after[3] == before[3] and after[4] != before[4]


def test_progress_output_hides_field_values(tmp_path, fake_env, fake_run, capsys):  # Review Focus #5
    assert main(["--ids", "3,4", "--out", str(tmp_path / "p.json")], clients_factory=fake_run()) == 0
    out = capsys.readouterr().out
    assert "[2/2]" in out
    for v in ("buildable_now", "buildable_gated", "blocked", "oauth2", "self_serve_free", "rule_violation"):
        assert v not in out


def test_cli_prints_utf8_safely(tmp_path, fake_env, fake_run, monkeypatch):  # Review Focus #1
    from agent.schema import AppSeed
    monkeypatch.setattr(run_mod, "load_apps",
                        lambda: [AppSeed(id=1, app="Café Ünïcode — “x”", category="C", hint="c.com")])
    assert main(["--ids", "1", "--out", str(tmp_path / "p.json")], clients_factory=fake_run()) == 0


def test_pilot_default_out_path_per_model(tmp_path, fake_env, fake_run, monkeypatch):  # D10
    monkeypatch.setattr("agent.config.RESULTS_DIR", tmp_path)
    assert main(["--pilot", "--model", "qwen/x:free"], clients_factory=fake_run()) == 0
    assert len(rows(tmp_path / "pilot" / "qwen_x_free.json")) == 10


def test_sample_note_when_unlabelled(tmp_path, fake_env, fake_run, monkeypatch, capsys):
    monkeypatch.setattr("agent.config.VERIFICATION_DIR", tmp_path)
    main(["--ids", "1", "--out", str(tmp_path / "p.json")], clients_factory=fake_run())  # id 1 is a sample app
    assert "sample apps included" in capsys.readouterr().out


def test_bundles_from_reuses_stored_bundles(tmp_path, fake_env, fake_run):
    out = tmp_path / "p.json"
    first = fake_run()
    main(["--ids", "3", "--out", str(out)], clients_factory=first)
    run_id = rows(out)[0]["meta"]["run_id"]
    second = fake_run()
    assert main(["--ids", "3", "--out", str(out), "--bundles-from", run_id], clients_factory=second) == 0
    assert second.tools.calls == 0 and second.llm.calls == 1


def test_tool_config_error_exits_3(tmp_path, fake_env, monkeypatch, capsys):
    from agent.tools import ToolConfigError

    class DeniedTools(FakeTools):
        async def search(self, query, **kw):
            raise ToolConfigError("COMPOSIO_API_KEY", "rejected by Composio (PermissionDeniedError)")

    def factory(settings, logger, budget, run_id):
        return FakeLLM(default=VALID), DeniedTools()
    assert main(["--ids", "3,4", "--out", str(tmp_path / "p.json")], clients_factory=factory) == 3
    assert "COMPOSIO_API_KEY" in capsys.readouterr().out
