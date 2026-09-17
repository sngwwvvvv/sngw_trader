# tests/test_strategies/test_kalman_spread_strategy.py
import math
import random
from decimal import Decimal

import pytest
from nautilus_trader.model import Bar, BarType, InstrumentId

from sngw_trader.indicators.execution_recovery import ExecutionState
from sngw_trader.strategies.kalman_spread import (
    PAIR_BANDS,
    EntryRequest,
    ExitRequest,
    KalmanSpreadConfig,
    KalmanSpreadStrategy,
    RehedgeRequest,
)

INSTRUMENT_IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")
BAR_TYPES = {
    iid: BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL") for iid in INSTRUMENT_IDS
}
HOUR_MS = 3_600_000


def _config(**overrides) -> KalmanSpreadConfig:
    base = dict(
        instrument_ids=INSTRUMENT_IDS,
        pair_ids=(1,),
        formation_hours=720,
        trading_hours=240,
    )
    base.update(overrides)
    return KalmanSpreadConfig(**base)


def _seeded_pair(n=720, seed=7):
    """Same generator as tests/test_indicators/test_pair_screening.py."""
    rng = random.Random(seed)
    x, e = 30_000.0, 0.0
    y_px, x_px = [], []
    for _ in range(n):
        x *= math.exp(rng.gauss(0.0, 0.001))
        e = 0.9714 * e + rng.gauss(0.0, 0.001)
        y_px.append(x * math.exp(e))
        x_px.append(x)
    return y_px, x_px


def _bar(symbol: str, ts_ms: int, close: float) -> Bar:
    return Bar.from_raw(
        bar_type=BAR_TYPES[f"{symbol}-USDT-SWAP.OKX"],
        open=Decimal(str(close - 1)),
        high=Decimal(str(close)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        price_prec=2,
        volume=Decimal("100"),
        size_prec=3,
        ts_event=ts_ms * 1_000_000,
        ts_init=ts_ms * 1_000_000,
    )


# --- pair table --------------------------------------------------------------


def test_pair_table_has_15_rows_with_spec_bands():
    assert len(PAIR_BANDS) == 15
    assert [b.pair_id for b in PAIR_BANDS] == list(range(1, 16))
    assert [b.book for b in PAIR_BANDS[:8]] == ["FACTOR"] * 8
    assert [b.book for b in PAIR_BANDS[8:]] == ["PEER"] * 7
    p1 = PAIR_BANDS[0]
    assert (p1.y_symbol, p1.x_symbol) == ("ETH-USDT-SWAP", "BTC-USDT-SWAP")
    assert (p1.entry_z, p1.exit_z, p1.stop_z) == (1.60, 0.30, 3.80)
    assert (p1.stop_duration_hours, p1.max_hold_hours, p1.hl_multiple) == (4, 96, 2.0)
    assert (p1.rehedge_frac, p1.cooldown_hours, p1.size_weight) == (0.10, 48, 1.00)
    assert (p1.sigma_multiple, p1.margin_cap_ratio, p1.mutex) == (1.00, 0.25, (9, 15))
    p15 = PAIR_BANDS[14]
    assert (p15.entry_z, p15.cooldown_hours, p15.mutex) == (1.90, 72, (1,))


def test_pair_table_mutex_targets_exist():
    ids = {b.pair_id for b in PAIR_BANDS}
    for band in PAIR_BANDS:
        for m in band.mutex:
            assert m in ids


# --- phase machine -----------------------------------------------------------


def _drive_formation(s: KalmanSpreadStrategy, hours: int, start_ms: int) -> int:
    rng = random.Random(11)
    x = 30_000.0
    ts = start_ms
    for _ in range(hours):
        x *= math.exp(rng.gauss(0.0, 0.001))
        y = x * 2.0 * math.exp(rng.gauss(0.0, 0.01))
        s.on_bar(_bar("BTC", ts, x))
        s.on_bar(_bar("ETH", ts, y))
        ts += HOUR_MS
    return ts


def test_formation_freezes_and_transitions_to_trading(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(True, 80, ()),
    )
    s = KalmanSpreadStrategy(config=_config())
    _drive_formation(s, 720, start_ms=0)
    rt = s._pairs[1]
    assert rt.phase == "TRADING"
    assert rt.hours == 0
    assert rt.kf is warm.filter
    assert rt.hl_hours == warm.hl_hours
    assert s._screen_results[1].passed is True


def test_screen_fail_resets_formation_window(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(False, 40, ("HURST_NOT_MR",)),
    )
    s = KalmanSpreadStrategy(config=_config())
    _drive_formation(s, 720, start_ms=0)
    rt = s._pairs[1]
    assert rt.phase == "FORMATION"
    assert rt.hours == 0 and not rt.y_marks
    assert s._screen_results[1].passed is False


def test_window_end_returns_to_formation(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(True, 80, ()),
    )
    s = KalmanSpreadStrategy(config=_config(formation_hours=72, trading_hours=24))
    ts = _drive_formation(s, 72, start_ms=0)
    assert s._pairs[1].phase == "TRADING"
    _drive_formation(s, 25, start_ms=ts)  # trading window + 1 bar
    assert s._pairs[1].phase == "FORMATION"
    assert s._pairs[1].hours == 0  # the 25th bar itself triggered the reset


def test_entry_crossing_emits_request():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase = "TRADING"
    rt.kf = _StubFilter([0.5, 1.7])
    s.on_bar(_bar("BTC", 0, 30_000.0))
    s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0))
    s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    entry = [r for r in s._request_log if isinstance(r, EntryRequest)]
    assert len(entry) == 1
    assert entry[0].side == -1 and entry[0].pair_id == 1  # z>0 cross -> short spread
    assert entry[0].y_mark > 0 and entry[0].x_mark > 0


def test_exit_mean_emits_request():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([0.1]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert len(exits) == 1 and exits[0].reason == "EXIT_MEAN"


def test_rehedge_request_on_beta_drift():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.entry_side, rt.entry_beta = "TRADING", 1, 1.0
    rt.kf = _StubFilter([0.5], beta=1.10)   # |1.10-1.0|/1.0 >= 0.10
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    rehedges = [r for r in s._request_log if isinstance(r, RehedgeRequest)]
    assert len(rehedges) == 1 and rehedges[0].beta == 1.10


def test_stop_z_requires_sustained_breach():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([4.0, 4.0, 4.0, 4.0]), -1
    for i in range(4):
        ts = i * HOUR_MS
        s.on_bar(_bar("BTC", ts, 30_000.0)); s.on_bar(_bar("ETH", ts, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_Z"   # only on the 4th sustained bar


def test_stop_flip_on_adverse_excursion():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([-1.2]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_FLIP"


def test_manual_kill_event_exits_emergency(tmp_path):
    events = tmp_path / "events.json"
    events.write_text(
        '[{"event_id":"e1","event_type":"MANUAL_KILL","symbol":"ETH-USDT-SWAP",'
        '"effective_at":0.0,"expires_at":null,"source_status":"ACTIVE"}]'
    )
    s = KalmanSpreadStrategy(config=_config(events_path=str(events)))
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([0.1]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_EVENT" and exits[-1].emergency is True
    assert rt.phase == "FORMATION"          # killed pair re-forms


def test_cooldown_blocks_reentry():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase = "TRADING"
    rt.kf = _StubFilter([0.5, 1.7])
    rt.exec_state = ExecutionState(cooldown_deadline=HOUR_MS)  # cooldown until 1h
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0)); s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    assert [r for r in s._request_log if isinstance(r, EntryRequest)] == []


def test_mutex_pair_blocks_entry():
    s = KalmanSpreadStrategy(config=_config(
        instrument_ids=INSTRUMENT_IDS + ("SOL-USDT-SWAP.OKX",), pair_ids=(1, 9)))
    rt1, rt9 = s._pairs[1], s._pairs[9]
    rt9.phase, rt9.entry_side = "TRADING", 1       # pair 9 open -> pair 1 mutexed
    rt1.phase, rt1.kf = "TRADING", _StubFilter([0.5, 1.7])
    s.on_bar(_bar("BTC", 0, 30_000.0))
    s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0))
    s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    assert [r for r in s._request_log if isinstance(r, EntryRequest)] == []


class _StubFilter:
    def __init__(self, z_values, beta=1.0):
        from sngw_trader.indicators.kalman_spread import KalmanSpreadResult

        self._results = [
            KalmanSpreadResult(0.001, 0.0, 0.001, z, beta, True, 72) for z in z_values
        ]

    def update(self, y_price, x_price):
        return self._results.pop(0)
