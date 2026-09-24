import json

from agent.observability import LOG_KEYS, RunLogger, new_run_id, read_log, redact, save_raw


def test_append_and_read(tmp_path):
    log = RunLogger(tmp_path / "l.jsonl")
    log.append({"kind": "llm", "cost_usd": 0.01})
    log.append({"kind": "tool"})
    recs = read_log(tmp_path / "l.jsonl")
    assert [r["kind"] for r in recs] == ["llm", "tool"]
    assert all(set(LOG_KEYS) <= set(r) for r in recs)


def test_creates_parent_dirs(tmp_path):
    RunLogger(tmp_path / "a" / "b" / "l.jsonl").append({"kind": "llm"})
    assert (tmp_path / "a" / "b" / "l.jsonl").exists()


def test_torn_last_line_ignored(tmp_path):
    p = tmp_path / "l.jsonl"
    p.write_text('{"kind":"llm"}\n{"kind":', encoding="utf-8")
    assert len(read_log(p)) == 1


def test_missing_log_is_empty(tmp_path):
    assert read_log(tmp_path / "nope.jsonl") == []


def test_secrets_redacted_everywhere(tmp_path):
    log = RunLogger(tmp_path / "l.jsonl", secrets=["ck_secret_value"])
    log.append({"error": "bad key sk-or-v1-deadbeef and ck_secret_value"})
    text = (tmp_path / "l.jsonl").read_text(encoding="utf-8")
    assert "deadbeef" not in text and "ck_secret_value" not in text
    assert "sk-or-v1-zzz" not in redact("x sk-or-v1-zzz y")


def test_non_ascii_logged_verbatim(tmp_path):
    RunLogger(tmp_path / "l.jsonl").append({"error": "café — “x”"})
    assert read_log(tmp_path / "l.jsonl")[0]["error"] == "café — “x”"


def test_save_raw_redacts_and_returns_posix(tmp_path):
    path = save_raw("run1", "llm_1.json", {"body": "sk-or-v1-abc and SECRETX"}, raw_dir=tmp_path, secrets=["SECRETX"])
    assert "\\" not in path and path.endswith("run1/llm_1.json")
    text = (tmp_path / "run1" / "llm_1.json").read_text(encoding="utf-8")
    assert "SECRETX" not in text and "sk-or-v1-abc" not in text and json.loads(text)


def test_run_id_format():
    rid = new_run_id()
    assert len(rid.split("-")[-1]) == 6 and rid[8] == "T"
