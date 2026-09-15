"""As-of alignment of macro inputs onto NYSE sessions -> RegimeSnapshot rows.

Cap-based as-of only: no unconditional ffill, no interpolation, and never a
`shift(1)` combined with a date cap for OAS (the calendar D-1 cap alone does
it). Master sessions are NYSE; the caller guarantees the list.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from typing import Callable

import numpy as np

from sngw_trader.data.regime_snapshot import make_snapshot
from sngw_trader.data.regime_universe import (
    STALE_SESSIONS,
    QUALITY_FRESH,
    QUALITY_INVALID,
    QUALITY_LAGGED,
    previous_calendar_date,
    session_age,
)
from sngw_trader.indicators.regime import (
    IQR_LONG,
    R0,
    growth_score,
    label_series,
    robust_z,
    stress_score,
)

_MISSING_AGE = -1
_NAN = float("nan")


def last_on_or_before(series: dict[date, float], cap: date) -> tuple[date, float] | None:
    """Latest observation with date <= cap, or None."""
    keys = sorted(series)
    i = bisect_right(keys, cap) - 1
    if i < 0:
        return None
    d = keys[i]
    return d, series[d]


def _align_one(
    series: dict[date, float], cap: date, session: date, sessions: list[date]
) -> tuple[float | None, str, int | None]:
    hit = last_on_or_before(series, cap)
    if hit is None:
        return None, "", None
    obs, val = hit
    return val, obs.isoformat(), session_age(obs, session, sessions)


def quality_for(
    oas_age: int | None,
    vix_age: int | None,
    vxv_age: int | None,
    copper_gold_age: int | None,
    has_vix_vxv: bool,
) -> int:
    """Aggregate input quality for one session.

    OAS fresh iff age == 1, lagged iff 1 < age <= 3, invalid iff missing or
    age > 3. VIX/VXV/copper-gold fresh iff age == 0, lagged iff 1 <= age <= 3,
    invalid iff missing or age > 3. Missing VIX or VXV makes the ratio
    impossible -> invalid. Any invalid wins, then any lagged.
    """

    def oas_status() -> int:
        if oas_age is None or oas_age > STALE_SESSIONS or oas_age < 1:
            return QUALITY_INVALID
        if oas_age == 1:
            return QUALITY_FRESH
        return QUALITY_LAGGED

    def spot_status(age: int | None) -> int:
        if age is None or age < 0 or age > STALE_SESSIONS:
            return QUALITY_INVALID
        if age == 0:
            return QUALITY_FRESH
        return QUALITY_LAGGED

    statuses = [oas_status(), spot_status(copper_gold_age)]
    if not has_vix_vxv:
        statuses.append(QUALITY_INVALID)
    else:
        statuses.append(spot_status(vix_age))
        statuses.append(spot_status(vxv_age))
    if QUALITY_INVALID in statuses:
        return QUALITY_INVALID
    if QUALITY_LAGGED in statuses:
        return QUALITY_LAGGED
    return QUALITY_FRESH


def align_session(
    session: date,
    sessions: list[date],
    oas: dict[date, float],
    vix: dict[date, float],
    vxv: dict[date, float],
    copper_gold_pairs: dict[date, float],
    copper: dict[date, float],
    gold: dict[date, float],
) -> dict:
    """Raw as-of inputs + ages + quality for one NYSE session.

    OAS cap = calendar D-1; every other series cap = D. CME-only days are
    never passed here (the master calendar is NYSE).
    """
    oas_val, oas_obs, oas_age = _align_one(oas, previous_calendar_date(session), session, sessions)
    vix_val, vix_obs, vix_age = _align_one(vix, session, session, sessions)
    vxv_val, vxv_obs, vxv_age = _align_one(vxv, session, session, sessions)
    cg_val, cg_obs, cg_age = _align_one(copper_gold_pairs, session, session, sessions)
    copper_val, copper_obs, _ = _align_one(copper, session, session, sessions)
    gold_val, gold_obs, _ = _align_one(gold, session, session, sessions)

    has_vix_vxv = vix_val is not None and vxv_val not in (None, 0.0)
    return {
        "oas_val": oas_val,
        "oas_obs": oas_obs,
        "oas_age": oas_age,
        "vix_val": vix_val,
        "vix_obs": vix_obs,
        "vix_age": vix_age,
        "vxv_val": vxv_val,
        "vxv_obs": vxv_obs,
        "vxv_age": vxv_age,
        "cg_val": cg_val,
        "cg_obs": cg_obs,
        "cg_age": cg_age,
        "copper_val": copper_val,
        "copper_obs": copper_obs,
        "gold_val": gold_val,
        "gold_obs": gold_obs,
        "has_vix_vxv": has_vix_vxv,
        "quality": quality_for(oas_age, vix_age, vxv_age, cg_age, has_vix_vxv),
    }


# ponytail: last_on_or_before re-sorts per call (O(n log n) each); fine to a
# few thousand sessions. Pre-sort keys + bisect in build_snapshots if slow.
def build_snapshots(
    sessions: list[date],
    oas: dict[date, float],
    vix: dict[date, float],
    vxv: dict[date, float],
    copper_gold_pairs: dict[date, float],
    copper: dict[date, float],
    gold: dict[date, float],
    feature_version: str,
    source_snapshot_id: str,
    ts_event_for: Callable[[date], int],
) -> list:
    """Per-NYSE-session RegimeSnapshot rows.

    Order: per-session raw as-of -> aligned series -> vectorized robust_z on
    OAS level, VIX/VXV ratio, copper/gold ratio -> stress/growth -> labels.
    Invalid sessions get regime_code=R0. Warmup sessions (before the long IQR
    window) are omitted entirely. Post-warmup sessions with NaN z (degenerate
    data, no label computable) are emitted as R0. Lagged-but-valid sessions
    keep sign-based quadrant labels.
    """
    rows = [
        align_session(s, sessions, oas, vix, vxv, copper_gold_pairs, copper, gold)
        for s in sessions
    ]

    def arr(key: str, ratio: bool = False) -> np.ndarray:
        vals = []
        for r in rows:
            v = r[key]
            if ratio:
                vv = r["vxv_val"]
                vals.append(v / vv if v is not None and vv else _NAN)
            else:
                vals.append(_NAN if v is None else v)
        return np.asarray(vals, dtype=float)

    oas_z = robust_z(arr("oas_val"))
    vix_vxv_z = robust_z(arr("vix_val", ratio=True))
    growth_z = robust_z(arr("cg_val"))
    stress = stress_score(oas_z, vix_vxv_z)
    growth = growth_score(growth_z)
    labels = label_series(stress, growth)

    snaps = []
    for t, (session, r) in enumerate(zip(sessions, rows)):
        if r["quality"] == QUALITY_INVALID:
            code = R0
        elif t < IQR_LONG - 1:
            continue  # warmup: no label exists yet, omit the session
        elif labels[t] == -1:
            code = R0  # NaN z past warmup: no quadrant computable
        else:
            code = int(labels[t])
        ts = ts_event_for(session)
        snaps.append(
            make_snapshot(
                ts_event=ts,
                ts_init=ts,
                session_date=session.isoformat(),
                oas=_NAN if r["oas_val"] is None else r["oas_val"],
                vix=_NAN if r["vix_val"] is None else r["vix_val"],
                vxv=_NAN if r["vxv_val"] is None else r["vxv_val"],
                copper=_NAN if r["copper_val"] is None else r["copper_val"],
                gold=_NAN if r["gold_val"] is None else r["gold_val"],
                copper_gold=_NAN if r["cg_val"] is None else r["cg_val"],
                vix_vxv=(r["vix_val"] / r["vxv_val"]) if r["has_vix_vxv"] else _NAN,
                oas_observation_date=r["oas_obs"],
                vix_observation_date=r["vix_obs"],
                vxv_observation_date=r["vxv_obs"],
                copper_observation_date=r["copper_obs"],
                gold_observation_date=r["gold_obs"],
                oas_age=_MISSING_AGE if r["oas_age"] is None else r["oas_age"],
                vix_age=_MISSING_AGE if r["vix_age"] is None else r["vix_age"],
                vxv_age=_MISSING_AGE if r["vxv_age"] is None else r["vxv_age"],
                copper_gold_age=_MISSING_AGE if r["cg_age"] is None else r["cg_age"],
                oas_z=float(oas_z[t]),
                vix_vxv_z=float(vix_vxv_z[t]),
                growth_z=float(growth_z[t]),
                stress=float(stress[t]),
                growth=float(growth[t]),
                regime_code=code,
                quality_code=r["quality"],
                feature_version=feature_version,
                source_snapshot_id=source_snapshot_id,
            )
        )
    return snaps
