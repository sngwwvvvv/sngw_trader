"""Stage 0A data-quality gate for the macro-proxy regime study.

Reads only catalog-derived plain structures (etf_dates, snapshots, manifest).
No HTTP, no writer imports. A failed gate must hard-stop Stage 0/1 via
`require_stage0a`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sngw_trader.data.regime_universe import (
    FEATURE_VERSION,
    QUALITY_LAGGED,
    REFERENCE_ETFS,
    SECTOR_ETFS,
)
from sngw_trader.research.regime_config import (
    DEFAULT_REGIME_CONFIG,
    RegimeExperimentConfig,
)

MIN_WARMUP_SESSIONS = 252


@dataclass
class Stage0AReport:
    ok: bool
    reasons: list[str]
    n_snapshots: int
    session_start: str | None
    session_end: str | None
    etf_rows: dict[str, int]
    quality_share: dict[str, float]
    r0_share: float
    lagged_share: float
    futures_quality: object
    feature_version: str | None
    source_snapshot_id: str | None
    notes: list[str] = field(default_factory=list)


def evaluate_stage0a(
    etf_dates: dict[str, set],
    snapshots: list,
    manifest: dict,
    config: RegimeExperimentConfig | None = None,
) -> Stage0AReport:
    config = config or DEFAULT_REGIME_CONFIG
    reasons: list[str] = []
    notes: list[str] = []

    for symbol in SECTOR_ETFS:
        if not etf_dates.get(symbol):
            reasons.append(f"ETF {symbol} has no sessions")

    present = [set(etf_dates[s]) for s in SECTOR_ETFS if etf_dates.get(s)]
    common = set.intersection(*present) if present else set()
    min_common = MIN_WARMUP_SESSIONS + config.MIN_REGIME_SESSIONS
    if len(common) < min_common:
        reasons.append(
            f"common sessions {len(common)} < {min_common} "
            f"(252 warmup + {config.MIN_REGIME_SESSIONS} regime)"
        )

    n = len(snapshots)
    if n < config.MIN_REGIME_SESSIONS:
        reasons.append(
            f"snapshots {n} < MIN_REGIME_SESSIONS {config.MIN_REGIME_SESSIONS}"
        )

    manifest_ver = manifest.get("feature_version")
    if manifest_ver != FEATURE_VERSION:
        reasons.append(
            f"manifest feature_version {manifest_ver!r} != {FEATURE_VERSION!r}"
        )
    snapshot_vers = {getattr(s, "feature_version", None) for s in snapshots}
    for ver in sorted(v for v in snapshot_vers if v != manifest_ver):
        reasons.append(f"snapshot feature_version {ver!r} != manifest {manifest_ver!r}")

    manifest_sid = manifest.get("source_snapshot_id")
    snapshot_sids = {getattr(s, "source_snapshot_id", None) for s in snapshots}
    for sid in sorted(s for s in snapshot_sids if s != manifest_sid):
        reasons.append(f"snapshot source_snapshot_id {sid!r} != manifest {manifest_sid!r}")

    session_dates = sorted(
        s for s in (getattr(x, "session_date", None) for x in snapshots) if s is not None
    )
    quality_counts: dict[str, int] = {"0": 0, "1": 0, "2": 0}
    r0 = 0
    lagged = 0
    for x in snapshots:
        qc = str(getattr(x, "quality_code", None))
        quality_counts[qc] = quality_counts.get(qc, 0) + 1
        if getattr(x, "regime_code", None) == 0:
            r0 += 1
        if getattr(x, "quality_code", None) == QUALITY_LAGGED:
            lagged += 1
    quality_share = {k: v / n for k, v in quality_counts.items()} if n else {}

    notes.append("VXV-driven start date, 2008 warmup inclusion, copper/gold spike "
                 "list, and bars without open are not computable from gate inputs")
    for symbol in REFERENCE_ETFS:
        if symbol in etf_dates and common:
            notes.append(f"{symbol} missing {len(common - etf_dates[symbol])} of {len(common)} common sessions")
        else:
            notes.append(f"{symbol} missing count unknown (reference ticker not in etf_dates)")

    return Stage0AReport(
        ok=not reasons,
        reasons=reasons,
        n_snapshots=n,
        session_start=session_dates[0] if session_dates else None,
        session_end=session_dates[-1] if session_dates else None,
        etf_rows={s: len(etf_dates.get(s) or ()) for s in SECTOR_ETFS},
        quality_share=quality_share,
        r0_share=r0 / n if n else 0.0,
        lagged_share=lagged / n if n else 0.0,
        futures_quality=manifest.get("futures_quality"),
        feature_version=manifest_ver,
        source_snapshot_id=manifest_sid,
        notes=notes,
    )


def require_stage0a(report: Stage0AReport) -> None:
    if not report.ok:
        raise SystemExit(
            "Stage 0A gate failed:\n" + "\n".join(f"- {r}" for r in report.reasons)
        )
