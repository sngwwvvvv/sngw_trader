"""Kalman MR spread strategy binding S01-S06 (S07 Phase 1).

One Strategy instance manages the 15-pair universe. Per pair: FORMATION
(720 completed 1h mark bars) freezes x_center/x_scale plus the screen
verdict, then TRADING (240 hours) trades spec §7.1 z-bands. Sizing, risk,
execution, events, and realized costs delegate to S03/S04/S05/S06/S02.
Strategy logic only — no runner assembly, no exchange I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderRejected
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.cost_model import (
    CostBreakdown,
    Fill,
    FundingObservation,
    calculate_cost,
)
from sngw_trader.indicators.contract_sizing import (
    ContractSpec,
    contract_spec_from_instrument,
    size_pair,
)
from sngw_trader.indicators.event_gate import (
    EXIT_EMERGENCY,
    EXIT_NONE,
    EXIT_ORDERLY,
    EventRecord,
    EventType,
    GateLimits,
    evaluate_gate,
)
from sngw_trader.indicators.execution_recovery import (
    CANCEL_UNFILLED,
    FLATTEN_FILLED,
    SUBMIT,
    ExecutionPhase,
    ExecutionState,
    FillObservation,
    LegStatus,
    begin_entry,
    begin_exit,
    confirm_flatten,
    emergency_flatten,
    on_fill,
    on_order_failure,
    on_timeout,
)
from sngw_trader.indicators.kalman_spread import KalmanSpreadFilter
from sngw_trader.indicators.pair_screening import (
    FormationStats,
    ScreenDecision,
    formation_stats,
    screen_pair,
)
from sngw_trader.indicators.portfolio_risk import (
    PairCandidate,
    PortfolioSnapshot,
    RiskLimits,
    approve_pair,
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
        self._contracts: dict[str, ContractSpec] = _load_contract_specs(config.contract_specs_path)
        self._funding: dict[str, dict[int, float]] = _load_funding_dir(config.funding_dir)
        self._order_seq = 0
        self._coid_legs: dict[str, tuple[int, str]] = {}
        self._rehedge_coids: set[str] = set()
        self._rehedge_fill_ids: set[str] = set()
        self._trade_records: list[dict] = []
        self._realized_pnl_usdt = 0.0
        self._equity_high = config.equity_usdt
        self._day_key = -1
        self._day_start_equity = config.equity_usdt
        self._last_ts_ms = 0

    # --- lifecycle -----------------------------------------------------------

    def on_start(self) -> None:
        for iid in self._instrument_ids:
            instrument_id = InstrumentId.from_str(iid)
            self.subscribe_bars(BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL"))

    # --- bar routing ---------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        self._last_ts_ms = bar.ts_event // 1_000_000
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
        pending, self._pending_requests = self._pending_requests, []
        self._request_log.extend(pending)
        for request in pending:
            rt = self._pairs[request.pair_id]
            if isinstance(request, EntryRequest):
                self._handle_entry(rt, request)
            elif isinstance(request, ExitRequest):
                self._handle_exit(rt, request)
            else:
                self._handle_rehedge(rt, request)

    # --- request execution ------------------------------------------------------

    def _handle_entry(self, rt: _PairRuntime, request: EntryRequest) -> None:
        cfg = self.config
        if rt.exec_state.phase not in (ExecutionPhase.IDLE, ExecutionPhase.CLOSED):
            return
        if any(self._pairs[m].entry_side != 0 for m in rt.spec.mutex if m in self._pairs):
            self.log.warning(f"pair {rt.spec.pair_id} entry dropped: mutex pair open")
            return
        target_n = self._target_notional(rt, request.beta)
        if target_n is None:
            return
        try:
            sizing = size_pair(
                target_notional_usdt=Decimal(str(target_n)),
                beta=Decimal(str(request.beta)),
                y_price=Decimal(str(request.y_mark)),
                x_price=Decimal(str(request.x_mark)),
                y_contract=self._contract_spec(f"{rt.spec.y_symbol}.OKX"),
                x_contract=self._contract_spec(f"{rt.spec.x_symbol}.OKX"),
            )
        except ValueError as exc:   # below minSz etc. -> skip this entry
            self.log.warning(f"pair {rt.spec.pair_id} sizing failed: {exc}")
            return
        candidate = PairCandidate(
            pair_id=str(rt.spec.pair_id),
            y_asset=rt.spec.y_symbol.split("-")[0],
            x_asset=rt.spec.x_symbol.split("-")[0],
            sizing=sizing,
            margin_usdt=sizing.gross_notional_usdt / Decimal(str(cfg.leverage)),
            cost=self._estimate_cost(rt, sizing),
            y_price_usdt=Decimal(str(request.y_mark)),
            x_price_usdt=Decimal(str(request.x_mark)),
            y_side=request.side,
            x_side=-request.side,
        )
        decision = approve_pair(self._snapshot(), candidate, self._risk_limits(rt.spec))
        if not decision.approved:
            self.log.warning(f"pair {rt.spec.pair_id} entry rejected: {decision.reasons}")
            return
        self._order_seq += 1
        y_coid = f"K{rt.spec.pair_id:02d}-Y-{self._order_seq}"
        x_coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
        deadline = request.ts_ms / 1000.0 + cfg.leg_timeout_hours * 3600
        result = begin_entry(rt.exec_state, sizing.y_quantity, sizing.x_quantity,
                             y_coid, x_coid, deadline, request.beta)
        rt.exec_state = result.state
        if result.reasons:
            return
        rt.entry_side = request.side
        rt.entry_beta = request.beta
        rt.entry_ts_ms = request.ts_ms
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.trade_fills = []
        rt.s02_fills = []
        rt.funding_obs = []
        rt.entry_avg = {}
        rt.decision_marks = {"Y": request.y_mark, "X": request.x_mark}
        for intent in result.actions:
            self._submit_intent(rt, intent, request.side)

    def _submit_intent(self, rt: _PairRuntime, intent, y_side: int) -> None:
        """y_side is the Y-leg side of the enclosing lifecycle step
        (+entry_side for entries, -entry_side for exits)."""
        if intent.kind == SUBMIT:
            leg = intent.leg
            side = y_side if leg == "Y" else -y_side
            self._submit_market(rt, leg, side, intent.quantity, intent.client_order_id)
        elif intent.kind == CANCEL_UNFILLED:
            self._cancel_order(intent.client_order_id)
        elif intent.kind == FLATTEN_FILLED:
            side = -self._held_side(rt, intent.leg)
            self._submit_market(rt, intent.leg, side, intent.quantity, self._next_coid(rt, intent.leg))

    def _handle_exit(self, rt: _PairRuntime, request: ExitRequest) -> None:
        phase = rt.exec_state.phase
        if phase is ExecutionPhase.OPEN:
            self._order_seq += 1
            y_coid = f"K{rt.spec.pair_id:02d}-Y-{self._order_seq}"
            x_coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
            result = begin_exit(rt.exec_state, y_coid, x_coid,
                                request.ts_ms / 1000.0 + self.config.leg_timeout_hours * 3600)
            rt.exec_state = result.state
            rt.exit_reason = request.reason
            rt.exit_emergency = request.emergency
            for intent in result.actions:   # SUBMIT with quantity = held qty
                self._submit_intent(rt, intent, -rt.entry_side)
        elif phase in (ExecutionPhase.ENTRY_PENDING, ExecutionPhase.ENTRY_PARTIAL):
            result = emergency_flatten(rt.exec_state)   # cancel pendings, flatten held
            rt.exec_state = result.state
            rt.exit_reason = request.reason
            rt.exit_emergency = request.emergency
            for intent in result.actions:
                self._submit_intent(rt, intent, rt.entry_side)
            if rt.exec_state.phase is ExecutionPhase.IDLE:
                self._reset_open_flags(rt)
        elif phase in (ExecutionPhase.EXIT_PENDING, ExecutionPhase.EXIT_PARTIAL,
                       ExecutionPhase.FLATTENING):
            rt.exit_reason = request.reason   # exit already in flight; just re-tag it
            rt.exit_emergency = request.emergency

    def _handle_rehedge(self, rt: _PairRuntime, request: RehedgeRequest) -> None:
        """Adjust only the X leg toward the new beta target (spec §2.4).
        Rehedge orders live outside the S05 entry/exit lifecycle; their fills
        patch the X leg target/filled directly."""
        if rt.exec_state.phase is not ExecutionPhase.OPEN:
            return
        x_mark = self._last_mark(f"{rt.spec.x_symbol}.OKX")
        y_mark = self._last_mark(f"{rt.spec.y_symbol}.OKX")
        target_n = self._target_notional(rt, request.beta)
        if target_n is None:
            return
        try:
            sizing = size_pair(
                target_notional_usdt=Decimal(str(target_n)),
                beta=Decimal(str(request.beta)),
                y_price=Decimal(str(y_mark)),
                x_price=Decimal(str(x_mark)),
                y_contract=self._contract_spec(f"{rt.spec.y_symbol}.OKX"),
                x_contract=self._contract_spec(f"{rt.spec.x_symbol}.OKX"),
            )
        except ValueError as exc:
            self.log.warning(f"pair {rt.spec.pair_id} rehedge sizing failed: {exc}")
            return
        x_spec = self._contract_spec(f"{rt.spec.x_symbol}.OKX")
        ct_val = float(x_spec.ct_val)
        held_coin = float(rt.exec_state.legs["X"].filled_quantity) * ct_val
        target_coin = float(sizing.x_quantity) * ct_val
        held_signed = -rt.entry_side * held_coin       # X held signed (long spread: X short)
        target_signed = -rt.entry_side * target_coin
        delta_signed = target_signed - held_signed
        if abs(delta_signed) < float(x_spec.min_sz) * ct_val:
            return
        self._order_seq += 1
        coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
        self._coid_legs[coid] = (rt.spec.pair_id, "X")
        self._rehedge_coids.add(coid)
        side = 1 if delta_signed > 0 else -1
        qty = (Decimal(str(abs(delta_signed) / ct_val)).quantize(x_spec.lot_sz))
        self._submit_market(rt, "X", side, qty, coid)
        rt.rehedge_target_x = sizing.x_quantity

    # --- fills, rejects, timeouts ------------------------------------------------

    def _apply_fill(self, rt: _PairRuntime, leg: str, fill_id: str, qty: Decimal,
                    price: Decimal, ts_ms: int, side: int) -> None:
        """side = order side of the fill (+1 buy, -1 sell); comes from the
        OrderFilled event (or the test)."""
        if rt.exec_state.phase is ExecutionPhase.FLATTENING:
            result = confirm_flatten(rt.exec_state, [FillObservation(leg, fill_id, qty, price, price)],
                                     cooldown_deadline=ts_ms / 1000.0 + rt.spec.cooldown_hours * 3600)
        else:
            reference = Decimal(str(rt.decision_marks.get(leg, float(price))))
            result = on_fill(rt.exec_state, FillObservation(leg, fill_id, qty, price, reference))
        late = "PHASE_NOT_ACTIVE" in result.reasons and result.state is rt.exec_state
        rt.exec_state = result.state
        for intent in result.actions:   # e.g. slippage rollback: cancel/flatten siblings
            self._submit_intent(rt, intent, rt.entry_side)
        if result.reasons == ("DUPLICATE_FILL",) or late:
            return
        signed_qty = self._signed_coin_qty(rt, leg, side, qty)
        inst_id = f"{rt.spec.y_symbol if leg == 'Y' else rt.spec.x_symbol}.OKX"
        rt.trade_fills.append((leg, signed_qty, float(price)))
        rt.s02_fills.append(Fill(leg, signed_qty, float(price),
                                 self.config.taker_fee * self.config.cost_multiplier,
                                 self._half_spread_rate(inst_id)))
        if rt.exec_state.phase is ExecutionPhase.OPEN:
            rt.entry_avg[leg] = float(price)
        if rt.exec_state.phase is ExecutionPhase.CLOSED:
            self._finalize_trade(rt, ts_ms)

    def _on_order_filled(self, event) -> None:
        coid = event.client_order_id.value
        mapping = self._coid_legs.get(coid)
        if mapping is None:
            return
        pair_id, leg = mapping
        rt = self._pairs[pair_id]
        qty = event.last_qty.as_decimal()
        price = event.last_px.as_decimal()
        side = 1 if event.order_side == OrderSide.BUY else -1
        fill_id = f"{coid}:{event.ts_init}"
        if coid in self._rehedge_coids:
            if fill_id in self._rehedge_fill_ids:
                return
            self._rehedge_fill_ids.add(fill_id)
            self._apply_rehedge_fill(rt, leg, qty, price, side)
            return
        self._apply_fill(rt, leg, fill_id, qty, price, event.ts_init // 1_000_000, side)

    def _apply_rehedge_fill(self, rt: _PairRuntime, leg: str, qty: Decimal, price: Decimal, side: int) -> None:
        """Rehedge fills patch the X leg target/filled directly (no S05 phase).
        filled_quantity is the held magnitude, so the signed coin delta flips
        sign against the held direction (-entry_side for the X leg)."""
        if rt.exec_state.phase is not ExecutionPhase.OPEN or rt.entry_side == 0:
            return   # stale fill after the lifecycle moved on
        x_spec = self._contract_spec(f"{rt.spec.x_symbol}.OKX")
        coin_delta = Decimal(str(float(qty) * float(x_spec.ct_val))) * side
        x_leg = rt.exec_state.legs["X"]
        new_filled = x_leg.filled_quantity + coin_delta * (-rt.entry_side) / x_spec.ct_val
        if new_filled < 0:
            new_filled = Decimal("0")
        new_target = rt.rehedge_target_x if rt.rehedge_target_x is not None else x_leg.target_quantity
        legs = dict(rt.exec_state.legs)
        legs["X"] = replace(x_leg, target_quantity=new_target, filled_quantity=new_filled,
                            status=LegStatus.FILLED)
        rt.exec_state = replace(rt.exec_state, legs=MappingProxyType(legs))
        signed_qty = float(qty) * float(x_spec.ct_val) * side
        rt.trade_fills.append(("X", signed_qty, float(price)))
        rt.s02_fills.append(Fill("X", signed_qty, float(price),
                                 self.config.taker_fee * self.config.cost_multiplier,
                                 self._half_spread_rate(f"{rt.spec.x_symbol}.OKX")))

    def on_event(self, event) -> None:
        if isinstance(event, OrderFilled):
            self._on_order_filled(event)
        elif isinstance(event, OrderRejected):
            mapping = self._coid_legs.get(event.client_order_id.value)
            if mapping is None:
                return
            rt = self._pairs[mapping[0]]
            leg = mapping[1]
            if (rt.exec_state.phase is ExecutionPhase.FLATTENING
                    and rt.exec_state.legs[leg].status is LegStatus.FLATTENING
                    and self._leg_held(rt, leg) > 0):
                # S05 on_order_failure ignores FLATTENING; a rejected flatten
                # order must be resubmitted or the pair wedges.
                side = -self._held_side(rt, leg)
                held = self._leg_held(rt, leg)
                self.log.warning(
                    f"pair {rt.spec.pair_id} flatten order rejected on {leg}; resubmitting"
                )
                self._submit_market(rt, leg, side, held, self._next_coid(rt, leg))
                return
            result = on_order_failure(rt.exec_state, leg)
            rt.exec_state = result.state
            for intent in result.actions:
                self._submit_intent(rt, intent, rt.entry_side)
            if rt.exec_state.phase is ExecutionPhase.CLOSED:
                self._finalize_trade(rt, self._last_ts_ms)
            elif rt.exec_state.phase is ExecutionPhase.IDLE:
                self._reset_open_flags(rt)

    def _reset_open_flags(self, rt: _PairRuntime) -> None:
        rt.entry_side = 0
        rt.entry_beta = None
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.trade_fills = []
        rt.s02_fills = []
        rt.funding_obs = []
        rt.entry_avg = {}

    def _check_timeout(self, rt: _PairRuntime, now_s: float):
        result = on_timeout(rt.exec_state, now_s)
        if result.state is not rt.exec_state:
            rt.exec_state = result.state
            for intent in result.actions:
                self._submit_intent(rt, intent, rt.entry_side)
            if rt.exec_state.phase is ExecutionPhase.CLOSED:
                self._finalize_trade(rt, int(now_s * 1000))
            elif rt.exec_state.phase is ExecutionPhase.IDLE:
                self._reset_open_flags(rt)
        return None

    # --- per-pair decision core ----------------------------------------------

    def _on_pair_close(self, rt: _PairRuntime, ts_ms: int, y_mark: float, x_mark: float) -> list:
        cfg = self.config
        spec = rt.spec
        now_s = ts_ms / 1000.0
        requests: list = []
        timeout_request = self._check_timeout(rt, now_s)
        if rt.phase == "FORMATION":
            if rt.entry_side != 0:
                self._accrue_funding(rt, ts_ms)   # exit still in flight across the reset
            rt.y_marks.append(y_mark)
            rt.x_marks.append(x_mark)
            rt.hours += 1
            if rt.hours >= cfg.formation_hours:
                self._complete_formation(rt, ts_ms)
            return requests
        rt.hours += 1
        gate_allowed, gate = self._evaluate_gate(rt, ts_ms)
        if gate == EXIT_EMERGENCY:
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
        """Cycle fields only. Position fields (entry_side/entry_beta/entry_ts_ms,
        exec_state) survive so an exit requested alongside the reset still
        dispatches with correct order sides; _reset_open_flags clears them at
        trade close / rollback."""
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
        """(corr(dFunding, e), max one-sided days) over the formation window.

        Funding history is wired for accrual and realized-cost accounting
        (_accrue_funding / _finalize_trade); these formation-screen inputs
        stay neutral 0 by design — using them is a documented Phase-2 ceiling.
        """
        return 0.0, 0

    # --- cost accounting and portfolio snapshot ----------------------------------

    def _finalize_trade(self, rt: _PairRuntime, ts_ms: int) -> None:
        fills = rt.s02_fills
        funding = rt.funding_obs
        expected = list(range(rt.entry_ts_ms // FUNDING_PERIOD_MS * FUNDING_PERIOD_MS + FUNDING_PERIOD_MS,
                              ts_ms, FUNDING_PERIOD_MS))
        cost, cost_error = None, None
        if fills and expected:
            try:
                cost = calculate_cost(fills, funding, expected_funding_timestamps=expected)
            except ValueError as exc:
                cost_error = str(exc)   # missing/duplicate funding fails closed (S02 contract)
        elif fills:
            cost_error = "no funding timestamps in holding period"
        pnl = -sum(signed * price for _, signed, price in rt.trade_fills)
        self._realized_pnl_usdt += pnl
        if cost is not None:
            self._realized_pnl_usdt -= cost.total_usdt
        self._trade_records.append({
            "pair_id": rt.spec.pair_id,
            "entry_ts_ms": rt.entry_ts_ms,
            "exit_ts_ms": ts_ms,
            "reason": rt.exit_reason or "UNKNOWN",
            "pnl_usdt": pnl,
            "cost": cost,
            "cost_error": cost_error,
        })
        rt.exec_state = replace(rt.exec_state,
                                cooldown_deadline=ts_ms / 1000.0 + rt.spec.cooldown_hours * 3600)
        self._reset_open_flags(rt)

    def _accrue_funding(self, rt: _PairRuntime, ts_ms: int) -> None:
        """Record S02 FundingObservations for both legs at OKX 8h stamps while open."""
        if rt.entry_side == 0 or ts_ms % FUNDING_PERIOD_MS != 0:
            return
        for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
            rate = self._funding.get(symbol, {}).get(ts_ms)
            if rate is None:
                continue   # absent observation -> calculate_cost fails closed at close
            mark = self._last_mark(f"{symbol}.OKX")
            held_side = self._held_side(rt, leg)
            ct_val = float(self._contract_spec(f"{symbol}.OKX").ct_val)
            rt.funding_obs.append(FundingObservation(
                leg, ts_ms, float(self._leg_held(rt, leg)) * ct_val * held_side, mark, rate))

    def _snapshot(self) -> PortfolioSnapshot:
        equity = Decimal(str(self._equity()))
        gross = Decimal(str(self._gross_notional()))
        margin_in_use = Decimal("0")
        for pid in self._open_pairs():
            rt = self._pairs[pid]
            for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
                ct_val = self._contract_spec(f"{symbol}.OKX").ct_val
                margin_in_use += (rt.exec_state.legs[leg].filled_quantity * ct_val
                                  * Decimal(str(self._last_mark(f"{symbol}.OKX"))))
        day = self._last_ts_ms // DAY_MS
        if day != self._day_key:
            self._day_key = day
            self._day_start_equity = float(equity)
        self._equity_high = max(self._equity_high, float(equity))
        return PortfolioSnapshot(
            equity_usdt=equity,
            peak_equity_usdt=Decimal(str(self._equity_high)),
            daily_loss_usdt=Decimal(str(max(0.0, self._day_start_equity - float(equity)))),
            liquidation_buffer_usdt=equity - margin_in_use,
            active_pairs=frozenset(str(p) for p in self._open_pairs()),
            gross_notional_usdt=gross,
            asset_exposure_usdt=self._asset_exposure(),
        )

    def _risk_limits(self, spec: PairBandSpec) -> RiskLimits:
        equity = Decimal(str(self._equity()))
        return RiskLimits(
            max_active_pairs=4,
            max_gross_exposure_ratio=Decimal("1.5"),
            max_pair_margin_ratio=Decimal(str(spec.margin_cap_ratio)),
            max_asset_exposure_usdt=equity * Decimal("1.5"),
            max_daily_loss_usdt=equity * Decimal("0.03"),
            max_drawdown_ratio=Decimal("0.15"),
            min_liquidation_buffer_usdt=Decimal("0"),
        )

    # --- small helpers ------------------------------------------------------------

    def _contract_spec(self, inst_id: str) -> ContractSpec:
        spec = self._contracts.get(inst_id)
        if spec is not None:
            return spec
        instrument = self.cache.instrument(InstrumentId.from_str(inst_id))
        if instrument is None:
            raise ValueError(f"no contract spec for {inst_id}")
        return contract_spec_from_instrument(instrument)

    def _last_mark(self, inst_id: str) -> float:
        bar = self._bars.get(InstrumentId.from_str(inst_id))
        if bar is None:
            raise ValueError(f"no mark yet for {inst_id}")
        return float(bar.close.as_decimal())

    def _half_spread_rate(self, inst_id: str) -> float:
        bps = self.config.btc_half_spread_bps if inst_id.startswith("BTC") else self.config.alt_half_spread_bps
        return bps / 10_000 * self.config.cost_multiplier

    def _target_notional(self, rt: _PairRuntime, beta: float) -> float | None:
        """N = equity * risk_frac * sigma_multiple * size_weight / sigma_u,
        clamped by the pair margin cap (margin = gross / leverage)."""
        if rt.sigma_u is None or rt.sigma_u <= 0:
            return None
        cfg = self.config
        equity = self._equity()
        n = equity * cfg.risk_frac * rt.spec.sigma_multiple * rt.spec.size_weight / rt.sigma_u
        gross = n * (1.0 + beta)
        cap = equity * rt.spec.margin_cap_ratio
        if gross > cap:
            n *= cap / gross
        return n

    def _estimate_cost(self, rt: _PairRuntime, sizing) -> CostBreakdown:
        """Entry-time round-trip estimate for the S04 gate (realized cost is
        accounted per fill via S02 at trade close)."""
        cfg = self.config
        gross = float(sizing.gross_notional_usdt)
        fee = 2 * cfg.taker_fee * cfg.cost_multiplier * gross
        spread = 2 * 0.5 * (self._half_spread_rate(f"{rt.spec.y_symbol}.OKX")
                            + self._half_spread_rate(f"{rt.spec.x_symbol}.OKX")) * gross
        funding = cfg.funding_rate_assumption * gross
        return CostBreakdown(fee, spread, funding, fee + spread + funding)

    def _equity(self) -> float:
        return self.config.equity_usdt + self._realized_pnl_usdt + self._unrealized_pnl()

    def _open_pairs(self) -> list[int]:
        return [pid for pid, rt in self._pairs.items() if rt.entry_side != 0]

    def _unrealized_pnl(self) -> float:
        total = 0.0
        for pid in self._open_pairs():
            rt = self._pairs[pid]
            for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
                avg = rt.entry_avg.get(leg)
                if avg is None:
                    continue
                ct_val = float(self._contract_spec(f"{symbol}.OKX").ct_val)
                held = (self._held_side(rt, leg)
                        * float(self._leg_held(rt, leg)) * ct_val)
                total += held * (self._last_mark(f"{symbol}.OKX") - avg)
        return total

    def _gross_notional(self) -> float:
        total = 0.0
        for pid in self._open_pairs():
            rt = self._pairs[pid]
            for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
                ct_val = float(self._contract_spec(f"{symbol}.OKX").ct_val)
                total += float(self._leg_held(rt, leg)) * ct_val * self._last_mark(f"{symbol}.OKX")
        return total

    def _asset_exposure(self) -> dict[str, Decimal]:
        exposure: dict[str, Decimal] = {}
        for pid in self._open_pairs():
            rt = self._pairs[pid]
            for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
                ct_val = self._contract_spec(f"{symbol}.OKX").ct_val
                notional = (self._held_side(rt, leg) * self._leg_held(rt, leg) * ct_val
                            * Decimal(str(self._last_mark(f"{symbol}.OKX"))))
                asset = symbol.split("-")[0]
                exposure[asset] = exposure.get(asset, Decimal("0")) + notional
        return exposure

    def _signed_coin_qty(self, rt: _PairRuntime, leg: str, side: int, qty: Decimal) -> float:
        """S03 sizes in contracts; S02/PnL accounting is in coin units."""
        symbol = rt.spec.y_symbol if leg == "Y" else rt.spec.x_symbol
        return float(qty) * float(self._contract_spec(f"{symbol}.OKX").ct_val) * side

    def _held_side(self, rt: _PairRuntime, leg: str) -> int:
        if rt.entry_side == 0:
            return 0
        return rt.entry_side if leg == "Y" else -rt.entry_side

    def _leg_held(self, rt: _PairRuntime, leg: str) -> Decimal:
        """Held magnitude: during an exit, filled_quantity is a progress
        counter, so what is still held is target - filled."""
        leg_state = rt.exec_state.legs[leg]
        if rt.exec_state.phase in (ExecutionPhase.EXIT_PENDING, ExecutionPhase.EXIT_PARTIAL):
            target = leg_state.target_quantity or Decimal("0")
            return max(target - leg_state.filled_quantity, Decimal("0"))
        return leg_state.filled_quantity

    def _next_coid(self, rt: _PairRuntime, leg: str) -> str:
        self._order_seq += 1
        coid = f"K{rt.spec.pair_id:02d}-{leg}-{self._order_seq}"
        self._coid_legs[coid] = (rt.spec.pair_id, leg)
        return coid

    def _submit_market(self, rt: _PairRuntime, leg: str, side: int, qty: Decimal, coid: str) -> None:
        symbol = rt.spec.y_symbol if leg == "Y" else rt.spec.x_symbol
        inst_id = f"{symbol}.OKX"
        spec = self._contract_spec(inst_id)
        precision = abs(spec.lot_sz.as_tuple().exponent)
        order = self.order_factory.market(
            instrument_id=InstrumentId.from_str(inst_id),
            order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
            quantity=Quantity(qty, precision),
            client_order_id=ClientOrderId(coid),
        )
        self.submit_order(order)
        self._coid_legs[coid] = (rt.spec.pair_id, leg)

    def _cancel_order(self, coid: str) -> None:
        order = self.cache.order(ClientOrderId(coid))
        if order is not None:
            self.cancel_order(order)


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


def _load_contract_specs(path: str) -> dict[str, ContractSpec]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return {
        inst_id: ContractSpec(Decimal(str(row["ct_val"])), Decimal(str(row["lot_sz"])),
                              Decimal(str(row["min_sz"])), int(row["price_precision"]))
        for inst_id, row in rows.items()
    }


def _load_funding_dir(path: str) -> dict[str, dict[int, float]]:
    if not path:
        return {}
    out: dict[str, dict[int, float]] = {}
    for file in Path(path).glob("*.json"):
        with open(file, encoding="utf-8") as f:
            rows = json.load(f)
        out[file.stem] = {int(ts): float(rate) for ts, rate in rows.items()}
    return out
