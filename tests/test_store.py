from agent.schema import EvidenceBundle
from agent.store import load_apps, load_bundle, load_results, load_split, save_bundle, write_results_atomic


def test_apps_load():
    apps = load_apps()
    assert len(apps) == 100 and apps[0].id == 1


def test_split_load():
    assert len(load_split("sample")) == 20 and len(load_split("pilot")) == 10


def test_results_roundtrip_non_ascii(tmp_path, make_row):  # Review Focus #1
    row = make_row(description="Zoho — “CRM” café", evidence={"auth_methods": [
        {"url": "https://ex.com", "quote": "Use “OAuth 2.0” — naïve café tokens"}]})
    p = tmp_path / "r.json"
    write_results_atomic(p, [row])
    assert load_results(p) == [row]
    assert "“OAuth 2.0”" in p.read_text(encoding="utf-8")


def test_atomic_write_sorted_and_no_tmp_left(tmp_path, make_row):
    p = tmp_path / "r.json"
    write_results_atomic(p, [make_row(id=5), make_row(id=2)])
    assert [r.id for r in load_results(p)] == [2, 5] and list(tmp_path.iterdir()) == [p]


def test_missing_results_is_empty(tmp_path):
    assert load_results(tmp_path / "none.json") == []


def test_bundle_roundtrip(tmp_path, make_bundle):
    b = make_bundle([("https://ex.com", "hello café")])
    save_bundle(b, raw_dir=tmp_path)
    assert load_bundle("r", 1, raw_dir=tmp_path) == b
    assert isinstance(load_bundle("r", 1, raw_dir=tmp_path), EvidenceBundle)



def test_atomic_write_retries_when_windows_locks_the_target(tmp_path, make_row, monkeypatch):
    import os

    import agent.store as store
    real, calls = os.replace, {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(store.os, "replace", flaky)
    monkeypatch.setattr(store, "_RETRY_DELAY_S", 0)
    p = tmp_path / "r.json"
    write_results_atomic(p, [make_row(id=2)])
    assert calls["n"] == 3 and [r.id for r in load_results(p)] == [2]
