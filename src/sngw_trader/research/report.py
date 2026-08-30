"""JSON report files + console summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def run_dir(strategy_name: str, base: Path | None = None) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = (base or Path("logs")) / strategy_name / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def write_reports(out_dir: Path, wf: dict, mc: dict, summary: dict) -> None:
    (out_dir / "wf_report.json").write_text(_dump(wf), encoding="utf-8")
    (out_dir / "mc_report.json").write_text(_dump(mc), encoding="utf-8")
    (out_dir / "summary.json").write_text(_dump(summary), encoding="utf-8")


def print_summary(summary: dict) -> None:
    print(_dump(summary))