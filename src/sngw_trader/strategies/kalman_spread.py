"""Kalman MR spread strategy binding S01-S06 (S07 Phase 1).

One Strategy instance manages the 15-pair universe. Per pair: FORMATION
(720 completed 1h mark bars) freezes x_center/x_scale plus the screen
verdict, then TRADING (240 hours) trades spec §7.1 z-bands. Sizing, risk,
execution, events, and realized costs delegate to S03/S04/S05/S06/S02.
Strategy logic only — no runner assembly, no exchange I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.event_gate import (
    EXIT_EMERGENCY,
    EXIT_NONE,
    EXIT_ORDERLY,
    EventRecord,
    EventType,
    GateLimits,
    evaluate_gate,
)
from sngw_trader.indicators.execution_recovery import ExecutionState
from sngw_trader.indicators.kalman_spread import KalmanSpreadFilter
from sngw_trader.indicators.pair_screening import (
    FormationStats,
    ScreenDecision,
    formation_stats,
    screen_pair,
)

HOUR_MS = 3_600_000
HOUR_NS = 3_600_000_000_000
DAY_MS = 86_400_000
FUNDING_PERIOD_MS = 8 * HOUR_MS
ACTIVE_EXEC_PHASES = frozenset(
    {"ENTRY_PENDING", "ENTRY_PARTIAL", "OPEN", "EXIT_PENDING", "EXIT_PARTIAL", "FLATTENING"}
)


@dataclass(frozen=True)
class PairBandSpec:
    pair_id: int
    book: str
    y_symbol: str
    x_symbol: str
    entry_z: float
    exit_z: float
    stop_z: float
    stop_duration_hours: int
    max_hold_hours: int
    hl_multiple: float
    rehedge_frac: float
    cooldown_hours: int
    size_weight: float
    sigma_multiple: float
    margin_cap_ratio: float
    mutex: tuple[int, ...] = ()


PAIR_BANDS: tuple[PairBandSpec, ...] = (
    PairBandSpec(1, "FACTOR", "ETH-USDT-SWAP", "BTC-USDT-SWAP", 1.60, 0.30, 3.80, 4, 96, 2.0, 0.10, 48, 1.00, 1.00, 0.25, (9, 15)),
    PairBandSpec(2, "FACTOR", "SOL-USDT-SWAP", "BTC-USDT-SWAP", 1.50, 0.30, 3.50, 3, 72, 2.0, 0.08, 48, 1.10, 1.00, 0.25, (9,)),
    PairBandSpec(3, "FACTOR", "BNB-USDT-SWAP", "BTC-USDT-SWAP", 1.70, 0.35, 3.40, 3, 60, 1.8, 0.08, 48, 0.80, 1.00, 0.20, ()),
    PairBandSpec(4, "FACTOR", "SUI-USDT-SWAP", "BTC-USDT-SWAP", 1.55, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.90, 1.00, 0.20, (10,)),
    PairBandSpec(5, "FACTOR", "XRP-USDT-SWAP", "BTC-USDT-SWAP", 1.80, 0.40, 3.20, 2, 48, 1.5, 0.10, 72, 0.60, 0.80, 0.15, ()),
    PairBandSpec(6, "FACTOR", "ADA-USDT-SWAP", "BTC-USDT-SWAP", 1.75, 0.30, 3.60, 4, 72, 2.0, 0.10, 48, 0.70, 1.00, 0.15, ()),
    PairBandSpec(7, "FACTOR", "AVAX-USDT-SWAP", "BTC-USDT-SWAP", 1.60, 0.30, 3.50, 3, 60, 1.8, 0.08, 48, 0.80, 1.00, 0.18, (12,)),
    PairBandSpec(8, "FACTOR", "DOGE-USDT-SWAP", "BTC-USDT-SWAP", 1.90, 0.40, 3.20, 2, 36, 1.5, 0.10, 72, 0.50, 0.70, 0.12, ()),
    PairBandSpec(9, "PEER", "ETH-USDT-SWAP", "SOL-USDT-SWAP", 1.90, 0.35, 3.40, 3, 48, 1.5, 0.08, 48, 0.50, 0.80, 0.15, (1, 2)),
    PairBandSpec(10, "PEER", "SUI-USDT-SWAP", "APT-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.55, 0.80, 0.12, (4,)),
    PairBandSpec(11, "PEER", "ARB-USDT-SWAP", "OP-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.55, 0.80, 0.12, ()),
    PairBandSpec(12, "PEER", "AVAX-USDT-SWAP", "NEAR-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.50, 0.80, 0.12, (7,)),
    PairBandSpec(13, "PEER", "AAVE-USDT-SWAP", "LINK-USDT-SWAP", 1.80, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.60, 0.90, 0.15, (14,)),
    PairBandSpec(14, "PEER", "UNI-USDT-SWAP", "AAVE-USDT-SWAP", 1.80, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.55, 0.90, 0.15, (13,)),
    PairBandSpec(15, "PEER", "LDO-USDT-SWAP", "ETH-USDT-SWAP", 1.90, 0.40, 3.20, 2, 36, 1.5, 0.10, 72, 0.45, 0.70, 0.10, (1,)),
)


@dataclass(frozen=True)
class EntryRequest:
    pair_id: int
    side: int          # +1 long spread (z<0), -1 short spread (z>0)
    beta: float
    y_mark: float
    x_mark: float
    ts_ms: int


@dataclass(frozen=True)
class ExitRequest:
    pair_id: int
    reason: str        # EXIT_MEAN | STOP_Z | STOP_TIME | STOP_FLIP | STOP_EVENT | WINDOW_END
    emergency: bool
    ts_ms: int


@dataclass(frozen=True)
class RehedgeRequest:
    pair_id: int
    beta: float
    ts_ms: int


@dataclass
class _PairRuntime:
    spec: PairBandSpec
    phase: str = "FORMATION"
    hours: int = 0
    y_marks: list[float] = field(default_factory=list)
    x_marks: list[float] = field(default_factory=list)
    kf: KalmanSpreadFilter | None = None
    hl_hours: float | None = None
    sigma_u: float | None = None
    prev_z: float | None = None
    stop_hours: int = 0
    hold_hours: int = 0
    entry_side: int = 0
    entry_beta: float | None = None
    entry_ts_ms: int = 0
    exec_state: ExecutionState = field(default_factory=ExecutionState)
    last_gate_check_s: float = 0.0
    decision_marks: dict[str, float] = field(default_factory=dict)   # leg -> decision-time mark
    trade_fills: list[tuple] = field(default_factory=list)           # (leg, signed_qty, price)
    s02_fills: list = field(default_factory=list)                    # S02 Fill records for calculate_cost
    funding_obs: list = field(default_factory=list)                  # S02 FundingObservation
    entry_avg: dict[str, float] = field(default_factory=dict)        # leg -> avg entry price
    exit_reason: str | None = None
    exit_emergency: bool = False
    rehedge_target_x: Decimal | None = None

    @property
    def has_exposure(self) -> bool:
        return self.exec_state.phase.name in ACTIVE_EXEC_PHASES


class KalmanSpreadConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    pair_ids: tuple[int, ...]
    formation_hours: int = 720
    trading_hours: int = 240
    r: float = 0.001
    delta: float = 0.0001
    equity_usdt: float = 10_000.0
    risk_frac: float = 0.005
    leverage: float = 2.0
    btc_half_spread_bps: float = 3.0
    alt_half_spread_bps: float = 6.0
    taker_fee: float = 0.0005
    cost_multiplier: float = 1.0
    funding_rate_assumption: float = 0.0004
    max_slippage_bps: float = 50.0
    leg_timeout_hours: float = 2.0
    gate_max_age_hours: float = 26.0
    funding_dir: str = ""
    events_path: str = ""
    contract_specs_path: str = ""


class KalmanSpreadStrategy(Strategy):
    """Kalman mean-reversion spread over the OKX USDT perp universe."""

    def __init__(self, config: KalmanSpreadConfig) -> None:
        super().__init__(config)
        wanted = set(config.pair_ids)
        self._pairs: dict[int, _PairRuntime] = {
            spec.pair_id: _PairRuntime(spec) for spec in PAIR_BANDS if spec.pair_id in wanted
        }
        self._instrument_ids = list(config.instrument_ids)
        self._bars: dict[InstrumentId, Bar] = {}
        self._processed_hours: set[tuple[int, int]] = set()
        self._events: tuple[EventRecord, ...] = _load_events(config.events_path)
        self._gate_limits = GateLimits(max_event_age_seconds=config.gate_max_age_hours * 3600)
        self._screen_results: dict[int, ScreenDecision] = {}
        self._pending_requests: list[EntryRequest | ExitRequest | RehedgeRequest] = []
        self._request_log: list[EntryRequest | ExitRequest | RehedgeRequest] = []

    # --- lifecycle -----------------------------------------------------------

    def on_start(self) -> None:
        for iid in self._instrument_ids:
            instrument_id = InstrumentId.from_str(iid)
            self.subscribe_bars(BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL"))

    # --- bar routing ---------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        inst = bar.bar_type.instrument_id
        self._bars[inst] = bar
        for rt in self._pairs.values():
            y_inst = InstrumentId.from_str(f"{rt.spec.y_symbol}.OKX")
            x_inst = InstrumentId.from_str(f"{rt.spec.x_symbol}.OKX")
            if inst not in (y_inst, x_inst):
                continue
            yb, xb = self._bars.get(y_inst), self._bars.get(x_inst)
            if yb is None or xb is None:
                continue
            hour = yb.ts_event // HOUR_NS
            if hour != xb.ts_event // HOUR_NS:
                continue
            key = (rt.spec.pair_id, hour)
            if key in self._processed_hours:
                continue
            self._processed_hours.add(key)
            requests = self._on_pair_close(
                rt,
                ts_ms=hour * HOUR_MS,
                y_mark=float(yb.close.as_decimal()),
                x_mark=float(xb.close.as_decimal()),
            )
            self._pending_requests.extend(requests)
        self._execute_requests()

    def _execute_requests(self) -> None:
        """Task 3 replaces this with real sizing/risk/execution; the audit
        log of every emitted request stays in both tasks."""
        self._request_log.extend(self._pending_requests)
        self._pending_requests = []

    # --- per-pair decision core ----------------------------------------------

    def _on_pair_close(self, rt: _PairRuntime, ts_ms: int, y_mark: float, x_mark: float) -> list:
        cfg = self.config
        spec = rt.spec
        now_s = ts_ms / 1000.0
        requests: list = []
        timeout_request = self._check_timeout(rt, now_s)   # Task 3; returns None in Phase 1
        if rt.phase == "FORMATION":
            rt.y_marks.append(y_mark)
            rt.x_marks.append(x_mark)
            rt.hours += 1
            if rt.hours >= cfg.formation_hours:
                self._complete_formation(rt, ts_ms)
            return requests
        rt.hours += 1
        gate_allowed, gate = self._evaluate_gate(rt, ts_ms)
        if gate == EXIT_EMERGENCY:
            # entry_side is the decision-core exposure flag until Task 3 wires fills
            if rt.has_exposure or rt.entry_side != 0:
                requests.append(ExitRequest(spec.pair_id, "STOP_EVENT", True, ts_ms))
            self._reset_to_formation(rt)
            return requests
        if rt.has_exposure and gate == EXIT_ORDERLY and rt.entry_side != 0:
            requests.append(ExitRequest(spec.pair_id, "STOP_EVENT", False, ts_ms))
            return requests
        if rt.hours > cfg.trading_hours:
            if rt.has_exposure:
                requests.append(ExitRequest(spec.pair_id, "WINDOW_END", False, ts_ms))
            self._reset_to_formation(rt)
            return requests
        if rt.kf is None:
            return requests
        res = rt.kf.update(y_mark, x_mark)
        if not res.ready:
            rt.prev_z = None
            return requests
        z = res.z_score
        rt.decision_marks = {"Y": y_mark, "X": x_mark}
        if rt.entry_side != 0:
            self._accrue_funding(rt, ts_ms)
            rt.hold_hours += 1
            request = self._exit_decision(rt, res, ts_ms)
            if request is not None:
                requests.append(request)
            elif (
                rt.entry_beta is not None
                and abs(res.beta - rt.entry_beta) / abs(rt.entry_beta) >= spec.rehedge_frac
            ):
                requests.append(RehedgeRequest(spec.pair_id, res.beta, ts_ms))
        else:
            request = self._entry_decision(rt, res, ts_ms, y_mark, x_mark, gate_allowed)
            if request is not None:
                requests.append(request)
        rt.prev_z = z
        return requests

    def _check_timeout(self, rt: _PairRuntime, now_s: float):
        """Task 3 implements leg-timeout flattening; stub keeps the call site
        (timeout runs before any decision) fixed."""
        return None

    def _exit_decision(self, rt: _PairRuntime, res, ts_ms: int):
        spec = rt.spec
        z = res.z_score
        if abs(z) <= spec.exit_z:
            return ExitRequest(spec.pair_id, "EXIT_MEAN", False, ts_ms)
        if abs(z) >= spec.stop_z:
            rt.stop_hours += 1
            if rt.stop_hours >= spec.stop_duration_hours:
                return ExitRequest(spec.pair_id, "STOP_Z", False, ts_ms)
        else:
            rt.stop_hours = 0
        if z * rt.entry_side > 1.0:
            return ExitRequest(spec.pair_id, "STOP_FLIP", False, ts_ms)
        max_hold = min(spec.max_hold_hours, spec.hl_multiple * (rt.hl_hours or spec.max_hold_hours))
        if rt.hold_hours >= max_hold:
            return ExitRequest(spec.pair_id, "STOP_TIME", False, ts_ms)
        return None

    def _entry_decision(self, rt: _PairRuntime, res, ts_ms: int, y_mark: float,
                        x_mark: float, gate_allowed: bool):
        spec = rt.spec
        if not gate_allowed or rt.prev_z is None:
            return None
        if (
            rt.exec_state.cooldown_deadline is not None
            and ts_ms / 1000.0 <= rt.exec_state.cooldown_deadline
        ):
            return None
        if any(self._pairs[m].entry_side != 0 for m in spec.mutex if m in self._pairs):
            return None
        if abs(rt.prev_z) <= spec.entry_z < abs(res.z_score):
            return EntryRequest(spec.pair_id, -1 if res.z_score > 0 else 1, res.beta,
                                y_mark, x_mark, ts_ms)
        return None

    def _accrue_funding(self, rt: _PairRuntime, ts_ms: int) -> None:
        """Task 3 wires the funding history; no-op keeps the call site fixed."""

    def _complete_formation(self, rt: _PairRuntime, ts_ms: int) -> None:
        cfg = self.config
        stats: FormationStats = formation_stats(rt.y_marks, rt.x_marks, cfg.r, cfg.delta)
        funding_corr, one_sided = self._funding_screen_inputs(rt, ts_ms)
        decision = screen_pair(stats, rt.spec.book, funding_corr=funding_corr,
                               funding_one_sided_days=one_sided,
                               cost_multiplier=cfg.cost_multiplier)
        self._screen_results[rt.spec.pair_id] = decision
        if decision.passed:
            rt.kf = stats.filter
            rt.hl_hours = stats.hl_hours
            rt.sigma_u = stats.sigma_u
            rt.phase = "TRADING"
        rt.hours = 0
        rt.y_marks = []
        rt.x_marks = []

    def _reset_to_formation(self, rt: _PairRuntime) -> None:
        rt.phase = "FORMATION"
        rt.hours = 0
        rt.y_marks = []
        rt.x_marks = []
        rt.kf = None
        rt.hl_hours = None
        rt.sigma_u = None
        rt.prev_z = None
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.entry_side = 0
        rt.entry_beta = None
        rt.entry_ts_ms = 0

    def _evaluate_gate(self, rt: _PairRuntime, ts_ms: int) -> tuple[bool, str]:
        """(entries_allowed, worst exit action) across both legs. With no
        event feed configured (empty events_path) the gate is neutral: the
        full curated stream is passed to S06 so per-symbol matching happens
        inside evaluate_gate and pairs without events are not MISSING_STATE."""
        if not self._events and not self.config.events_path:
            return True, EXIT_NONE
        now_s = ts_ms / 1000.0
        last = rt.last_gate_check_s if rt.last_gate_check_s > 0 else now_s
        rt.last_gate_check_s = now_s
        allowed, worst = True, EXIT_NONE
        for symbol in (rt.spec.y_symbol, rt.spec.x_symbol):
            decision = evaluate_gate(self._events, symbol, now_s, last, self._gate_limits)
            allowed = allowed and decision.entries_allowed
            if decision.exit_action == EXIT_EMERGENCY:
                return allowed, EXIT_EMERGENCY
            if decision.exit_action == EXIT_ORDERLY:
                worst = EXIT_ORDERLY
        return allowed, worst

    def _funding_screen_inputs(self, rt: _PairRuntime, ts_ms: int) -> tuple[float, int]:
        """(corr(dFunding, e), max one-sided days) over the formation window."""
        return 0.0, 0   # Task 3 wires the funding history; 0 keeps gates neutral


def _load_events(path: str) -> tuple[EventRecord, ...]:
    if not path:
        return ()
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return tuple(
        EventRecord(
            event_id=row["event_id"],
            event_type=EventType(row["event_type"]),
            symbol=row.get("symbol"),
            effective_at=row["effective_at"],
            expires_at=row.get("expires_at"),
            source_status=row.get("source_status", "ACTIVE"),
            observed_at=row.get("observed_at", row["effective_at"]),
        )
        for row in rows
    )
