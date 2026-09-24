"""File I/O for seed data, splits, results and evidence bundles (UTF-8 everywhere, atomic writes)."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from agent import config
from agent.schema import AppResult, AppSeed, EvidenceBundle


def load_apps(path: Path | None = None) -> list[AppSeed]:
    path = Path(path) if path else config.DATA_DIR / "apps.json"
    return [AppSeed.model_validate(a) for a in json.loads(path.read_text(encoding="utf-8"))]


def load_split(name: Literal["sample", "pilot"]) -> list[int]:
    return list(json.loads((config.DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))["ids"])


def load_results(path: Path) -> list[AppResult]:
    path = Path(path)
    if not path.exists():
        return []
    return [AppResult.model_validate(r) for r in json.loads(path.read_text(encoding="utf-8"))]


def write_json_atomic(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_results_atomic(path: Path, rows: Iterable[AppResult]) -> None:
    write_json_atomic(path, [r.to_json_dict() for r in sorted(rows, key=lambda r: r.id)])


def bundle_path(run_id: str, app_id: int, raw_dir: Path | None = None) -> Path:
    return (Path(raw_dir) if raw_dir else config.RAW_DIR) / run_id / f"bundle_{app_id}.json"


def save_bundle(bundle: EvidenceBundle, raw_dir: Path | None = None) -> str:
    path = bundle_path(bundle.run_id, bundle.app_id, raw_dir)
    write_json_atomic(path, bundle.model_dump(mode="json"))
    return path.as_posix()


def load_bundle(run_id: str, app_id: int, raw_dir: Path | None = None) -> EvidenceBundle:
    path = bundle_path(run_id, app_id, raw_dir)
    return EvidenceBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))
