"""Append-only run log (`results/runs/run_log.jsonl`) and raw payload capture.

One JSON line per LLM call and per tool call (SPEC §2.10). Everything written passes through
`redact`, so a secret can never reach disk even if a caller passes it by mistake.
"""
from __future__ import annotations

import json
import re
import secrets as _secrets
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent import config

LOG_KEYS = ("kind", "run_id", "ts", "app_id", "stage", "model", "prompt_version", "tokens_in", "tokens_out",
            "cost_usd", "latency_ms", "retries", "status", "error", "schema_valid", "tool", "url", "http_status",
            "raw_path", "cost_estimated")
_KEY_PATTERN = re.compile(r"sk-or-v1-[A-Za-z0-9_\-]+")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + _secrets.token_hex(3)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[REDACTED]")
    return _KEY_PATTERN.sub("[REDACTED]", text)


class RunLogger:
    def __init__(self, path: Path | None = None, secrets: Iterable[str] = ()):
        self.path = Path(path) if path else config.RUN_LOG
        self.secrets = [s for s in secrets if s]
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = {k: None for k in LOG_KEYS}
        line.update(record)
        line["ts"] = line.get("ts") or utc_now()
        text = redact(json.dumps(line, ensure_ascii=False, default=str), self.secrets)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")


def read_log(path: Path | None = None) -> list[dict]:
    path = Path(path) if path else config.RUN_LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn last line after a crash
    return out


def save_raw(run_id: str, name: str, obj: Any, raw_dir: Path | None = None, secrets: Iterable[str] = ()) -> str:
    """Write a raw payload under <raw_dir>/<run_id>/<name>; return a posix path (repo-relative when possible)."""
    base = Path(raw_dir) if raw_dir else config.RAW_DIR
    target = base / run_id / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(redact(json.dumps(obj, ensure_ascii=False, indent=1, default=str), secrets), encoding="utf-8")
    try:
        return target.resolve().relative_to(config.ROOT).as_posix()
    except ValueError:
        return target.as_posix()
