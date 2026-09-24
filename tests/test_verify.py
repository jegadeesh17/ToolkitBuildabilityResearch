import json
from pathlib import Path

import pytest

from agent.pipeline import load_prompt
from agent.schema import SCORED_FIELDS, AppSeed, Flag
from agent.verify import (
    CONFIDENCE,
    MAX_RERESEARCH_TOOL_CALLS,
    VerifyContext,
    judge_prompt,
    main,
    reresearch_prompt,
    same_value,
    verify_row,
)
from tests.conftest import TEST_ENV, FakeTools
from tests.test_pipeline import DOC, PAGE_TEXT, VALID, VALID_DICT

APP = AppSeed(id=3, app="Pipe", category="CRM", hint="pipe.com")
NEW = "https://pipe.com/docs/api-reference"
NEW_TEXT = ("The Pipe API reference lists a moderate set of endpoints for deals and persons only. " * 10)
BREADTH_QUOTE = "with full create, read, update and delete"


class ScriptedLLM:
    """Replies per stage, in order; records (stage, model) of every call."""

    def __init__(self, by_stage):
        self.by_stage = {k: list(v) for k, v in by_stage.items()}
        self.calls = []

    async def chat(self, **kw):
        from agent.llm_client import LLMResponse
        self.calls.append((kw["stage"], kw["model"]))
        queue = self.by_stage.get(kw["stage"]) or []
        if not queue:
            raise AssertionError(f"no scripted reply for stage {kw['stage']}")
        return LLMResponse(queue.pop(0), kw["model"], 10, 10, 0.001, False, 5, 0, "raw.json")


def reply(**over):
    d = json.loads(VALID)
    ev = over.pop("evidence", None)
    d.update(over)
    if ev:
        d["evidence"].update(ev)
    return json.dumps(d)


def judge_reply(**fields):
    return json.dumps({"fields": {f: {"value": v, "evidence": [{"url": u, "quote": q}] if u else [], "reason": "r"}
                                  for f, (v, u, q) in fields.items()}})


QUERIES = json.dumps({"queries": ["Pipe API reference endpoints", "Pipe developer docs"]})


def p1_row(make_row, **over):
    d = dict(id=3, app="Pipe", category="CRM", **{k: VALID_DICT[k] for k in SCORED_FIELDS},
             evidence=VALID_DICT["evidence"])
    d.update(over)
    return make_row(**d)


def ctx(llm, tools, run_logger, loops=("grounding", "cross", "judge", "reresearch")):
    return VerifyContext(llm=llm, tools=tools, logger=run_logger, verify_model="v/m", judge_model="j/m",
                         run_id="v1", loops=set(loops), prompt=load_prompt(), judge=judge_prompt(),
                         rer=reresearch_prompt())


@pytest.fixture
def bundle(make_bundle):
    return make_bundle([(DOC, PAGE_TEXT)])


async def test_agreement_is_high_confidence_with_no_diff(make_row, bundle, run_logger):
    llm, tools = ScriptedLLM({"verify": [VALID]}), FakeTools()
    row, stats = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, tools, run_logger))
    assert row.confidence == CONFIDENCE["agree"] and row.pass2_diff == [] and row.flags == []
    assert row.meta.pass_ == 2 and "verify v/m" in row.meta.model and llm.calls == [("verify", "v/m")]
    assert tools.calls == 0 and stats["disputed"] == 0


async def test_disagreement_is_resolved_by_judge(make_row, bundle, run_logger):
    llm = ScriptedLLM({"verify": [reply(api_breadth="moderate")],
                       "judge": [judge_reply(api_breadth=("moderate", DOC, BREADTH_QUOTE))]})
    row, stats = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, FakeTools(), run_logger))
    assert row.api_breadth == "moderate" and Flag.CROSS_MODEL_DISAGREE in row.flags
    [diff] = row.pass2_diff
    assert (diff.field, diff.before, diff.after, diff.resolved_by) == ("api_breadth", "broad", "moderate", "judge")
    assert row.confidence == round((6 * 0.9 + 0.7) / 7, 4) and stats["judged"] == 1
    assert ("judge", "j/m") in llm.calls


async def test_ungrounded_judge_answer_goes_to_reresearch(make_row, bundle, run_logger):
    llm = ScriptedLLM({"verify": [reply(api_breadth="moderate")],
                       "judge": [judge_reply(api_breadth=("moderate", DOC, "a sentence that is not on the page"))],
                       "reresearch": [QUERIES, reply(api_breadth="moderate", evidence={"api_breadth": [
                           {"url": NEW, "quote": "lists a moderate set of endpoints for deals and persons"}]})]})
    tools = FakeTools({NEW: NEW_TEXT}, [NEW])
    row, stats = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, tools, run_logger))
    assert row.api_breadth == "moderate" and row.pass2_diff[0].resolved_by == "reresearch"
    assert row.confidence == round((6 * 0.9 + 0.4) / 7, 4) and Flag.NEEDS_HUMAN not in row.flags
    assert stats["tool_calls"] == tools.calls == 3  # 2 searches + 1 new page
    assert Flag.GROUNDING_FAILED not in row.flags  # new page is part of the pass-2 bundle


async def test_unresolved_keeps_pass1_value_and_needs_human(make_row, bundle, run_logger):
    llm = ScriptedLLM({"verify": [reply(api_breadth="moderate")],
                       "judge": [judge_reply(api_breadth=("unknown", None, None))],
                       "reresearch": [QUERIES, reply(api_breadth="unknown",
                                                     unknown_reason={"api_breadth": "model_unsure"})]})
    row, stats = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, FakeTools({NEW: NEW_TEXT}, [NEW]),
                                                                     run_logger))
    assert row.api_breadth == "broad" and row.pass2_diff == [] and Flag.NEEDS_HUMAN in row.flags
    assert row.confidence == round((6 * 0.9 + 0.2) / 7, 4) and stats["unresolved"] == 1


async def test_reresearch_tool_calls_are_capped(make_row, bundle, run_logger):
    many = [f"https://pipe.com/docs/p{i}" for i in range(10)]
    llm = ScriptedLLM({"verify": [reply(api_breadth="moderate")], "judge": [judge_reply()],
                       "reresearch": [QUERIES, reply()]})
    tools = FakeTools({u: NEW_TEXT for u in many}, many)
    _, stats = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, tools, run_logger))
    assert tools.calls == stats["tool_calls"] <= MAX_RERESEARCH_TOOL_CALLS


async def test_loops_subset_skips_judge_and_reresearch(make_row, bundle, run_logger):
    llm, tools = ScriptedLLM({"verify": [reply(api_breadth="moderate")]}), FakeTools()
    row, _ = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, tools, run_logger, ("grounding", "cross")))
    assert [c[0] for c in llm.calls] == ["verify"] and tools.calls == 0
    assert row.api_breadth == "broad" and Flag.NEEDS_HUMAN in row.flags


async def test_both_unknown_goes_straight_to_reresearch(make_row, bundle, run_logger):
    p1 = p1_row(make_row, api_breadth="unknown", unknown_reason={"api_breadth": "model_unsure"},
                evidence={k: v for k, v in VALID_DICT["evidence"].items() if k != "api_breadth"})
    llm = ScriptedLLM({"verify": [reply(api_breadth="unknown", unknown_reason={"api_breadth": "model_unsure"})],
                       "reresearch": [QUERIES, reply(api_breadth="narrow", evidence={"api_breadth": [
                           {"url": NEW, "quote": "lists a moderate set of endpoints for deals and persons"}]})]})
    row, stats = await verify_row(p1, bundle, APP, ctx(llm, FakeTools({NEW: NEW_TEXT}, [NEW]), run_logger))
    assert "judge" not in [c[0] for c in llm.calls] and row.api_breadth == "narrow"
    assert row.pass2_diff[0].resolved_by == "reresearch" and "api_breadth" not in row.unknown_reason


async def test_rule_violation_fields_go_to_judge(make_row, bundle, run_logger):
    p1 = p1_row(make_row, blocker="paid_plan_required")  # rule 2 violated, both models agree
    llm = ScriptedLLM({"verify": [reply(blocker="paid_plan_required")],
                       "judge": [judge_reply(
                           blocker=("none", DOC, "Free developer sandbox accounts are available"),
                           verdict=("buildable_now", DOC, "Free developer sandbox accounts are available"),
                           access_model=("self_serve_free", DOC, "Free developer sandbox accounts are available"))]})
    row, stats = await verify_row(p1, bundle, APP, ctx(llm, FakeTools(), run_logger))
    assert stats["judged"] == 3 and row.blocker == "none" and Flag.RULE_VIOLATION not in row.flags


async def test_no_pages_skips_cross_model(make_row, make_bundle, run_logger):
    p1 = p1_row(make_row, **{f: "unknown" for f in SCORED_FIELDS}, evidence={},
                unknown_reason={f: "fetch_failed" for f in SCORED_FIELDS})
    llm = ScriptedLLM({"reresearch": [QUERIES, reply(evidence={f: [{"url": NEW, "quote":
                                                                    "lists a moderate set of endpoints for deals"}]
                                                                 for f in SCORED_FIELDS})]})
    row, _ = await verify_row(p1, make_bundle([]), APP, ctx(llm, FakeTools({NEW: NEW_TEXT}, [NEW]), run_logger))
    assert "verify" not in [c[0] for c in llm.calls] and row.meta.pass_ == 2


async def test_pass2_rows_pass_validation(make_row, bundle, run_logger):
    from agent.validate import validate_rows
    llm = ScriptedLLM({"verify": [reply(api_breadth="moderate")],
                       "judge": [judge_reply(api_breadth=("moderate", DOC, BREADTH_QUOTE))]})
    row, _ = await verify_row(p1_row(make_row), bundle, APP, ctx(llm, FakeTools(), run_logger))
    assert validate_rows([row.to_json_dict()], [APP], 1) == []


def test_same_value_is_order_insensitive_for_sets():
    assert same_value("auth_methods", ["api_key", "oauth2"], ["oauth2", "api_key"])
    assert not same_value("verdict", "buildable_now", "blocked")


def test_verify_never_reads_ground_truth():
    src = Path("agent/verify.py").read_text(encoding="utf-8")
    assert "ground_truth" not in src and "agent.score" not in src


# --- CLI -------------------------------------------------------------------------------------------------

@pytest.fixture
def pass1_file(tmp_path, make_row, make_bundle):
    from agent.store import save_bundle, write_results_atomic
    meta = {"pass": 1, "run_id": "p1run", "model": "p/m", "prompt_version": "p1-x", "updated_at": "t"}
    rows = []
    for app_id, app, cat in ((3, "Pipedrive", "CRM and Sales"), (4, "Attio", "CRM and Sales")):
        rows.append(make_row(id=app_id, app=app, category=cat, meta=meta,
                             **{k: VALID_DICT[k] for k in SCORED_FIELDS}, evidence=VALID_DICT["evidence"]))
        save_bundle(make_bundle([(DOC, PAGE_TEXT)]).model_copy(update={"run_id": "p1run", "app_id": app_id}))
    p = tmp_path / "pass1.json"
    write_results_atomic(p, rows)
    return p


@pytest.fixture
def verify_env(monkeypatch):
    for k, v in TEST_ENV.items():
        monkeypatch.setenv(k, v)


def factory_with(llm):
    def f(settings, logger, budget, run_id):
        return llm, FakeTools()
    return f


def test_cli_writes_valid_pass2(tmp_path, pass1_file, verify_env, capsys):
    from agent.validate import main as validate_main
    out = tmp_path / "pass2.json"
    llm = ScriptedLLM({"verify": [VALID, VALID]})
    assert main(["--input", str(pass1_file), "--output", str(out)], clients_factory=factory_with(llm)) == 0
    assert validate_main([str(out), "--expect", "2"]) == 0
    text = capsys.readouterr().out
    assert "buildable_now" not in text  # progress stays blind


def test_cli_missing_verify_model_exit_3(pass1_file, verify_env, monkeypatch):
    monkeypatch.delenv("VERIFY_MODEL")
    assert main(["--input", str(pass1_file)]) == 3


def test_cli_bad_loops_exit_2(pass1_file):
    assert main(["--input", str(pass1_file), "--loops", "grounding,magic"]) == 2


def test_cli_budget_stop_exit_4_and_resume(tmp_path, pass1_file, verify_env, monkeypatch):
    import agent.config as config
    from agent.observability import RunLogger
    RunLogger(config.RUN_LOG).append({"kind": "llm", "cost_usd": 9.0})
    out = tmp_path / "pass2.json"
    assert main(["--input", str(pass1_file), "--output", str(out)],
                clients_factory=factory_with(ScriptedLLM({}))) == 4
    monkeypatch.setenv("BUDGET_CAP_USD", "20")
    llm = ScriptedLLM({"verify": [VALID, VALID]})
    assert main(["--input", str(pass1_file), "--output", str(out), "--resume"], clients_factory=factory_with(llm)) == 0
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2
