"""CLI: run pass 1 on a selection of apps (SPEC §2.9).

python -m agent.run (--app NAME | --ids 1,2 | --pilot | --sample | --all) [--stage pass1] [--model SLUG]
                    [--resume] [--out PATH] [--bundles-from RUN_ID] [--concurrency 4]

Exit codes: 0 ok · 1 some apps errored (rerun with --resume) · 2 usage/unknown app · 3 missing config
· 4 budget cap. Progress lines never print researched values (keeps the sample blind).
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent import config
from agent.budget import BudgetExceeded, BudgetGuard
from agent.config import ConfigError, config_error_message, load_settings, secret_values
from agent.cost_report import render, summarize
from agent.observability import RunLogger, new_run_id, read_log, redact
from agent.pipeline import Prompt, load_prompt, research_app
from agent.schema import AppResult, AppSeed
from agent.store import load_apps, load_bundle, load_results, load_split, write_results_atomic


class UsageError(Exception):
    pass


@dataclass
class RunSummary:
    done: int = 0
    skipped: int = 0
    errored: list[int] = field(default_factory=list)
    budget_stopped: bool = False
    evidence_dropped: int = 0
    grounded: int = 0
    total_claims: int = 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m agent.run", description="Run pass 1 of the research agent.")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--app", help="one app by name (case-insensitive)")
    sel.add_argument("--ids", help="comma-separated app ids, e.g. 1,2,5")
    sel.add_argument("--pilot", action="store_true", help="the 10 pilot apps (data/pilot.json)")
    sel.add_argument("--sample", action="store_true", help="the 20 held-out sample apps (data/sample.json)")
    sel.add_argument("--all", action="store_true", help="all 100 apps")
    ap.add_argument("--stage", choices=["pass1"], default="pass1")
    ap.add_argument("--model", help="OpenRouter model slug (default: PASS1_MODEL)")
    ap.add_argument("--resume", action="store_true", help="skip apps already present in the output file")
    ap.add_argument("--out", help="output JSON (default results/pass1.json; pilot: results/pilot/<model>.json)")
    ap.add_argument("--bundles-from", metavar="RUN_ID", help="reuse evidence bundles saved by an earlier run")
    ap.add_argument("--concurrency", type=int, default=4)
    return ap


def select_apps(args: argparse.Namespace, apps: list[AppSeed]) -> list[AppSeed]:
    by_id = {a.id: a for a in apps}
    if args.app:
        match = [a for a in apps if a.app.lower() == args.app.strip().lower()]
        if not match:
            raise UsageError(f"Unknown app '{args.app}'.")
        return match
    if args.ids:
        try:
            ids = [int(x) for x in args.ids.split(",")]
        except ValueError:
            raise UsageError(f"--ids must be comma-separated integers, got '{args.ids}'.") from None
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            raise UsageError(f"Unknown app id(s): {unknown}.")
        return [by_id[i] for i in dict.fromkeys(ids)]
    if args.pilot:
        return [by_id[i] for i in load_split("pilot")]
    if args.sample:
        return [by_id[i] for i in load_split("sample")]
    return list(apps)


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", model).strip("_")


def output_path(args: argparse.Namespace, model: str) -> Path:
    if args.out:
        return Path(args.out)
    if args.pilot:
        return config.RESULTS_DIR / "pilot" / f"{model_slug(model)}.json"
    return config.RESULTS_DIR / "pass1.json"


def default_clients(settings, logger: RunLogger, budget: BudgetGuard, run_id: str) -> tuple[Any, Any]:
    from agent.llm_client import LLMClient
    from agent.tools import ToolClient
    return LLMClient(settings, logger, budget, run_id=run_id), ToolClient(settings, logger, run_id=run_id)


async def run_apps(apps: list[AppSeed], *, llm: Any, tools: Any, logger: RunLogger, budget: BudgetGuard, model: str,
                   stage: str, out_path: Path, resume: bool, bundles_from: str | None, concurrency: int,
                   prompt: Prompt, run_id: str) -> RunSummary:
    existing: dict[int, AppResult] = {r.id: r for r in load_results(out_path)}
    todo = [a for a in apps if not (resume and a.id in existing)]
    summary = RunSummary(skipped=len(apps) - len(todo))
    sem = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()
    stop = asyncio.Event()
    fatal: list[ConfigError] = []

    async def one(app: AppSeed) -> None:
        async with sem:
            if stop.is_set():
                return
            try:
                budget.check()
                bundle = load_bundle(bundles_from, app.id) if bundles_from else None
                row, stats = await research_app(app, llm=llm, tools=tools, logger=logger, model=model, stage=stage,
                                                run_id=run_id, prompt=prompt, bundle=bundle)
            except BudgetExceeded as e:
                summary.budget_stopped = True
                stop.set()
                print(f"  id={app.id} not started: {e}")
                return
            except ConfigError as e:
                fatal.append(e)
                stop.set()
                return
            except Exception as e:  # one app's failure must not kill the run; --resume retries it
                logger.append({"kind": "app_error", "run_id": run_id, "app_id": app.id, "stage": stage,
                               "model": model, "status": "error",
                               "error": redact(f"{type(e).__name__}: {e}", logger.secrets)[:300]})
                summary.errored.append(app.id)
                print(f"  id={app.id} status=error ({type(e).__name__})")
                return
            async with lock:
                existing[row.id] = row
                write_results_atomic(out_path, existing.values())
                summary.done += 1
                summary.evidence_dropped += stats["evidence_dropped"]
                summary.grounded += stats["grounded"]
                summary.total_claims += stats["total_claims"]
                print(f"[{summary.done}/{len(todo)}] id={app.id} status=ok pages={stats['pages']} "
                      f"unknown={stats['unknown_fields']} flags={len(row.flags)} spent=${budget.spent:.4f}")

    try:
        await asyncio.gather(*(one(a) for a in todo))
    finally:
        closer = getattr(llm, "aclose", None)
        if closer:
            await closer()
    if fatal:
        raise fatal[0]
    return summary


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None, *, clients_factory: Callable | None = None) -> int:
    _utf8_stdout()
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as e:
        return 0 if e.code in (0, None) else 2
    apps = load_apps()
    try:
        selected = select_apps(args, apps)
    except UsageError as e:
        print(f"{e} Valid apps:")
        print("\n".join(f"  {a.id:>3}  {a.app}" for a in apps))
        return 2
    try:
        settings = load_settings()
    except ConfigError as e:
        print(config_error_message(e))
        return 3

    model = args.model or settings.pass1_model
    stage = "pilot" if args.pilot else args.stage
    out_path = output_path(args, model)
    labelled = (config.VERIFICATION_DIR / "ground_truth.json").exists()
    if {a.id for a in selected} & set(load_split("sample")) and not labelled:
        print("NOTE: sample apps included; do not open their rows until blind labelling is exported.")
    run_id = new_run_id()
    logger = RunLogger(config.RUN_LOG, secrets=secret_values(settings))
    budget = BudgetGuard(settings.budget_cap_usd, config.RUN_LOG)
    print(f"run {run_id}: {len(selected)} app(s) · model={model} · stage={stage} · out={out_path} · "
          f"cap=${settings.budget_cap_usd:.2f} (spent so far ${budget.spent:.4f})")
    try:
        llm, tools = (clients_factory or default_clients)(settings, logger, budget, run_id)
        summary = asyncio.run(run_apps(selected, llm=llm, tools=tools, logger=logger, budget=budget, model=model,
                                       stage=stage, out_path=out_path, resume=args.resume,
                                       bundles_from=args.bundles_from, concurrency=args.concurrency,
                                       prompt=load_prompt(), run_id=run_id))
    except ConfigError as e:
        print(config_error_message(e))
        return 3

    rate = f"{summary.grounded / summary.total_claims:.1%}" if summary.total_claims else "n/a"
    print(f"done={summary.done} skipped={summary.skipped} errored={summary.errored or 0} "
          f"grounded={summary.grounded}/{summary.total_claims} ({rate}) evidence_dropped={summary.evidence_dropped}")
    print(render(summarize(read_log(config.RUN_LOG), run_id=run_id)))
    if summary.budget_stopped:
        print(f"Budget cap reached (BUDGET_CAP_USD=${settings.budget_cap_usd:.2f}). Progress is saved; "
              "raise the cap deliberately and rerun with --resume.")
        return 4
    if summary.errored:
        print(f"{len(summary.errored)} app(s) errored: {summary.errored}. Rerun with --resume to retry them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
