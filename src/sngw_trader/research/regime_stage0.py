"""Stage 0 forward probe: do regime quadrants split forward sector-ETF returns?

Trade-free. Pure functions first; main() loads the catalog, enforces the
Stage 0A gate, and writes a JSON report under logs/regime_stage0/<utc>/.
No HTTP, no yahoo_etf/fred_macro imports, no runner imports.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.data.regime_snapshot import RegimeSnapshot
from sngw_trader.data.regime_universe import (
    ALL_EVAL_ETFS,
    CYC_ETFS,
    DEF_ETFS,
    QUALITY_INVALID,
    SECTOR_ETFS,
    instrument_id_for,
)
from sngw_trader.indicators.regime import (
    IQR_LONG,
    MEDIAN_WINDOW,
    R0,
    label_series,
    robust_z,
    stress_score,
)
from sngw_trader.research.regime_config import (
    DEFAULT_REGIME_CONFIG,
    RegimeExperimentConfig,
)
from sngw_trader.research.regime_stage0a import evaluate_stage0a, require_stage0a

MAIN_BLOCK = 60  # W=60 main run
BAR_TYPE_SUFFIX = "-1-DAY-LAST-EXTERNAL"
MIN_DURATION_MEDIAN = 10

LINES: dict[str, tuple[str, ...]] = {
    **{s: (s,) for s in SECTOR_ETFS},
    "cyc": CYC_ETFS,
    "def": DEF_ETFS,
    "spy": ("SPY",),
    "qqq": ("QQQ",),
    "iwm": ("IWM",),
}


def fwd_return(
    open_px: dict[date, float], t: date, h: int, sessions: list[date]
) -> float | None:
    """Open[t+1] -> Open[t+h] return; None when any open is missing (no close sub)."""
    i = sessions.index(t)
    if i + h >= len(sessions) or h < 1:
        return None
    o1 = open_px.get(sessions[i + 1])
    oH = open_px.get(sessions[i + h])
    if o1 is None or oH is None:
        return None
    return oH / o1 - 1


def basket_fwd(
    member_opens: dict[str, dict[date, float]], t: date, h: int, sessions: list[date]
) -> float | None:
    """Equal-weight forward return; None unless ALL members have opens."""
    rets = [fwd_return(m, t, h, sessions) for m in member_opens.values()]
    if any(r is None for r in rets):
        return None
    return sum(rets) / len(rets)


def block_bootstrap_mean_ci(
    values: np.ndarray, block: int, iters: int, seed: int
) -> tuple[float, float, float]:
    """Moving-block bootstrap CI for the mean: (mean, p5, p95).

    Overlapping forward observations are not independent, so resample in
    contiguous blocks instead of iid.
    """
    x = np.asarray(values, dtype=float)
    n = len(x)
    if n == 0:
        raise ValueError("block_bootstrap_mean_ci: empty values")
    block = max(1, min(block, n))
    n_blocks = int(np.ceil(n / block))
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1, size=(iters, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(iters, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return (
        float(x.mean()),
        float(np.percentile(means, 5)),
        float(np.percentile(means, 95)),
    )


def regime_durations(codes: list[int]) -> dict[int, list[int]]:
    """Consecutive run lengths per code (R0 runs kept but excluded from pass)."""
    out: dict[int, list[int]] = {}
    prev: int | None = None
    for c in codes:
        if c == prev:
            out[c][-1] += 1
        else:
            out.setdefault(c, []).append(1)
            prev = c
    return out


def stage0_pass(
    table: dict, config: RegimeExperimentConfig | None
) -> tuple[bool, list[str]]:
    """Validation-window pass criteria. R2/R3 report-only. z0 is never retuned."""
    config = config or DEFAULT_REGIME_CONFIG
    reasons: list[str] = []
    if not table["r1_cyc_20"] > table["r1_def_20"]:
        reasons.append("R1: cyc 20d fwd mean <= def")
    if not table["r1_cyc_20"] >= table["r1_spy_20"]:
        reasons.append("R1: cyc 20d fwd mean < spy")
    if not table["r4_cyc_p5"] < table["r4_def_p5"]:
        reasons.append("R4: cyc p5 not worse than def p5")
    if not table["r4_cyc_p5"] < table["r1_cyc_p5"]:
        reasons.append("R4: cyc p5 not worse than R1 cyc p5")
    if not table["r4_cyc_20"] < table["r4_spy_20"]:
        reasons.append("R4: cyc 20d fwd mean >= spy")
    if table["duration_median_r1_r4"] < MIN_DURATION_MEDIAN:
        reasons.append(f"R1-R4 duration median < {MIN_DURATION_MEDIAN}")
    for r in ("r1", "r4"):
        n = table[f"n_{r}"]
        share = table[f"share_{r}"]
        if n < config.MIN_REGIME_SESSIONS:
            reasons.append(
                f"{r.upper()} n {n} < MIN_REGIME_SESSIONS {config.MIN_REGIME_SESSIONS}"
            )
        if share < config.MIN_REGIME_SHARE:
            reasons.append(
                f"{r.upper()} share {share:.3f} < MIN_REGIME_SHARE {config.MIN_REGIME_SHARE}"
            )
    return (not reasons, reasons)


def _window_of(session_date: str, config: RegimeExperimentConfig) -> str:
    if session_date <= config.DEV_END:
        return "dev"
    if session_date <= config.VALIDATION_END:
        return "validation"
    return "test"


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"mean": None, "median": None, "p5": None, "n": 0}
    a = np.asarray(vals, dtype=float)
    return {
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "p5": float(np.percentile(a, 5)),
        "n": int(len(a)),
    }


def _eval_window(
    name: str,
    session_codes: dict[date, int],
    sessions: list[date],
    member_opens: dict[str, dict[str, dict[date, float]]],
    config: RegimeExperimentConfig,
    block: int,
) -> dict:
    n_total = len(session_codes)
    by_code: dict[int, list[date]] = {}
    for d in sorted(session_codes):
        by_code.setdefault(session_codes[d], []).append(d)

    ci_h = max(config.FWD_HORIZONS)
    regimes: dict[str, dict] = {}
    excluded = 0
    for code, dates in sorted(by_code.items()):
        lines: dict[str, dict] = {}
        for line, m_opens in member_opens.items():
            per_h: dict[str, dict] = {}
            for h in config.FWD_HORIZONS:
                vals: list[float] = []
                n_exc = 0
                for d in dates:
                    r = basket_fwd(m_opens, d, h, sessions)
                    if r is None:
                        n_exc += 1
                    else:
                        vals.append(r)
                excluded += n_exc
                entry = _stats(vals)
                entry["n_excluded_missing_open"] = n_exc
                if h == ci_h and vals:
                    entry["ci"] = block_bootstrap_mean_ci(
                        vals, block, config.BOOTSTRAP_ITERS, config.BOOTSTRAP_SEED
                    )
                per_h[str(h)] = entry
            lines[line] = per_h
        regimes[str(code)] = {
            "n": len(dates),
            "share": len(dates) / n_total if n_total else 0.0,
            "lines": lines,
        }

    durs = regime_durations([session_codes[d] for d in sorted(session_codes)])
    durations = {
        str(c): {"runs": runs, "median": float(np.median(runs))}
        for c, runs in sorted(durs.items())
    }
    runs_14 = [ln for c in (1, 2, 3, 4) for ln in durs.get(c, [])]
    dur_median_14 = float(np.median(runs_14)) if runs_14 else None

    def _mean(code: int, line: str) -> float | None:
        return regimes.get(str(code), {}).get("lines", {}).get(line, {}).get(
            str(ci_h), {}
        ).get("mean")

    def _p5(code: int, line: str) -> float | None:
        return regimes.get(str(code), {}).get("lines", {}).get(line, {}).get(
            str(ci_h), {}
        ).get("p5")

    table: dict = {}
    for code in (1, 2, 3, 4):
        for line in ("cyc", "def", "spy"):
            table[f"r{code}_{line}_20"] = _mean(code, line)
        table[f"r{code}_cyc_p5"] = _p5(code, "cyc")
        table[f"r{code}_def_p5"] = _p5(code, "def")
    table["duration_median_r1_r4"] = dur_median_14
    for code in (1, 4):
        r = regimes.get(str(code))
        table[f"n_r{code}"] = r["n"] if r else 0
        table[f"share_r{code}"] = r["share"] if r else 0.0
    table["n_total"] = n_total

    return {
        "window": name,
        "n_total": n_total,
        "n_excluded_missing_open": excluded,
        "regimes": regimes,
        "durations": durations,
        "table": table,
    }


def rescored_codes(snapshots: list, median_window: int) -> dict[str, int]:
    """Re-derive regime_code per session at a different robust_z median window.

    Reuses robust_z/stress_score/label_series on the raw aligned inputs stored
    on the snapshots (oas, vix_vxv, copper_gold). Alignment and quality are
    window-independent, so only the z median window changes; invalid-quality
    and NaN-z sessions stay R0 (same rules as build_snapshots).

    Snapshots omit the warmup segment (first IQR_LONG-1 aligned sessions), so
    z at emitted index j is exact only once the long IQR window lies entirely
    inside the stored series (j >= IQR_LONG-1); earlier sessions are labeled
    R0 (not exactly rescorable) instead of silently biased.
    """
    snaps = sorted(snapshots, key=lambda s: s.session_date)

    def col(getter) -> np.ndarray:
        return np.asarray([float(getter(s)) for s in snaps], dtype=float)

    oas_z = robust_z(col(lambda s: s.oas), median_window=median_window)
    vix_vxv_z = robust_z(col(lambda s: s.vix_vxv), median_window=median_window)
    growth_z = robust_z(col(lambda s: s.copper_gold), median_window=median_window)
    labels = label_series(stress_score(oas_z, vix_vxv_z), growth_z)
    codes: dict[str, int] = {}
    for j, (s, lab) in enumerate(zip(snaps, labels)):
        if s.quality_code == QUALITY_INVALID or lab == -1 or j < IQR_LONG - 1:
            codes[s.session_date] = R0
        else:
            codes[s.session_date] = int(lab)
    return codes


def run_stage0(
    sessions: list[date],
    opens: dict[str, dict[date, float]],
    snapshots: list,
    config: RegimeExperimentConfig | None = None,
    block: int = MAIN_BLOCK,
    median_window: int | None = None,
    robustness: bool = False,
) -> dict:
    """JSON-serializable Stage 0 probe over dev/validation/test windows.

    `block` is the moving-block bootstrap length (MAIN_BLOCK=60, kept fixed
    across runs). `median_window` re-scores regime codes via robust_z at that
    median window: None keeps the stored W=60 codes; the one-shot robustness
    appendix uses config.robustness_window (120) and is never adopted.
    """
    config = config or DEFAULT_REGIME_CONFIG
    member_opens = {
        line: {m: opens[m] for m in members} for line, members in LINES.items()
    }
    if median_window is None:
        codes_all = {
            s.session_date: int(s.regime_code) for s in snapshots
        }
    else:
        codes_all = rescored_codes(snapshots, median_window)
    windows = {}
    for name in ("dev", "validation", "test"):
        codes = {
            date.fromisoformat(k): c
            for k, c in codes_all.items()
            if _window_of(k, config) == name
        }
        windows[name] = _eval_window(name, codes, sessions, member_opens, config, block)
    return {
        "block": block,
        "median_window": median_window if median_window is not None else MEDIAN_WINDOW,
        "robustness": robustness,
        "horizons": list(config.FWD_HORIZONS),
        "lines": sorted(LINES),
        "windows": windows,
    }


def validation_verdict(
    main_run: dict, config: RegimeExperimentConfig | None = None
) -> tuple[bool, list[str]]:
    """Pass flag comes only from the Validation window of the W=60 main run."""
    table = main_run["windows"]["validation"]["table"]
    required = (
        "r1_cyc_20", "r1_def_20", "r1_spy_20",
        "r4_cyc_20", "r4_spy_20",
        "r4_cyc_p5", "r4_def_p5", "r1_cyc_p5",
        "duration_median_r1_r4",
    )
    missing = [k for k in required if table.get(k) is None]
    if missing:
        return False, [f"missing table entries: {', '.join(missing)}"]
    return stage0_pass(table, config)


def _load_bars(catalog: ParquetDataCatalog) -> tuple[dict, dict]:
    bar_types = [
        f"{instrument_id_for(s)}{BAR_TYPE_SUFFIX}" for s in ALL_EVAL_ETFS
    ]
    opens: dict[str, dict[date, float]] = {}
    etf_dates: dict[str, set[date]] = {}
    for bar in catalog.bars(bar_types=bar_types):
        sym = str(bar.bar_type).split("-")[0].split(".")[0]
        d = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).date()
        opens.setdefault(sym, {})[d] = float(bar.open)
        etf_dates.setdefault(sym, set()).add(d)
    return opens, etf_dates


def main() -> None:
    settings = load_settings()
    catalog = ParquetDataCatalog(str(settings.catalog_path))
    manifest = json.loads(
        (settings.catalog_path / "regime_manifest.json").read_text(encoding="utf-8")
    )
    opens, etf_dates = _load_bars(catalog)
    snaps = [cd.data for cd in catalog.custom_data(cls=RegimeSnapshot)]
    report0a = evaluate_stage0a(etf_dates, snaps, manifest)
    require_stage0a(report0a)

    sessions = sorted(set.intersection(*(etf_dates[s] for s in SECTOR_ETFS)))
    config = DEFAULT_REGIME_CONFIG
    run = run_stage0(sessions, opens, snaps, config, block=MAIN_BLOCK)
    robust = run_stage0(
        sessions, opens, snaps, config, block=MAIN_BLOCK,
        median_window=config.robustness_window, robustness=True,
    )
    ok, reasons = validation_verdict(run, config)

    payload = {
        **run,
        "robustness": robust,
        "pass": {"ok": ok, "reasons": reasons},
        "stage0a": {
            "ok": report0a.ok,
            "n_snapshots": report0a.n_snapshots,
            "session_start": report0a.session_start,
            "session_end": report0a.session_end,
        },
    }
    out_dir = Path("logs/regime_stage0") / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "report.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    verdict = "PASS" if ok else "FAIL"
    print(
        f"Stage 0 {verdict} ({len(reasons)} reasons) "
        f"sessions={len(sessions)} snapshots={len(snaps)} -> {out_path}"
    )
