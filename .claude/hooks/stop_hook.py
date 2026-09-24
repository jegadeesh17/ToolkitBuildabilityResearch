"""Claude Code Stop hook: run the offline test suite before Claude may finish a turn.

Behaviour
- No tests yet (no tests/test_*.py, or pytest collects nothing / exit 5): allow stop.
- Tests pass: allow stop.
- Tests fail: block the stop and feed the failing tail back to Claude.
- Already blocked once this turn (stop_hook_active) and still failing: allow stop
  but warn the user, so a stuck fix loop cannot spin forever.

Uses the project's .venv interpreter when present. Never reads or prints .env.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
TIMEOUT_S = 300
TAIL_LINES = 40


def emit(payload: dict) -> None:
    print(json.dumps(payload))
    sys.exit(0)


def python_exe() -> str:
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python"):
        candidate = ROOT / rel
        if candidate.exists():
            return str(candidate)
    return sys.executable


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        hook_input = {}
    already_blocked = bool(hook_input.get("stop_hook_active"))

    tests_dir = ROOT / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.rglob("test_*.py")):
        sys.exit(0)  # Milestone 0 not built yet: nothing to enforce

    # Tests must run offline; strip keys so a test can never make a paid call.
    env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "COMPOSIO_API_KEY")}
    try:
        proc = subprocess.run(
            [python_exe(), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        code, output = -1, f"pytest timed out after {TIMEOUT_S}s"
    else:
        code, output = proc.returncode, (proc.stdout + proc.stderr)

    if code in (0, 5):  # 5 = no tests collected
        sys.exit(0)

    tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
    if code == 1 and "No module named pytest" in output:
        reason = "pytest is not installed in the project interpreter. Run: pip install -r requirements.txt"
    else:
        reason = f"Test suite failed (pytest exit {code}). Fix the failures before declaring the task done.\n\n{tail}"

    if already_blocked:
        emit({"systemMessage": f"Stop hook: tests are STILL failing (pytest exit {code}); stopping anyway to avoid a loop. Do not treat this task as done."})
    emit({"decision": "block", "reason": reason})


if __name__ == "__main__":
    main()
