from types import SimpleNamespace

import pandas as pd

from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import (
    RunResult,
    build_strategy,
    extract_equity_marks,
    extract_analyzer_equity_marks,
    extract_fills,
    extract_run_result,
)

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.example.ema_cross:EMACross",
    config_path="sngw_trader.strategies.example.ema_cross:EMACrossConfig",
    fixed={"trade_size": "0.01"},
    grid={},
)


def test_build_strategy_from_path_and_params():
    s = build_strategy(
        SPEC,
        {"fast_ema_period": 21, "slow_ema_period": 55},
        "BTC-USDT-SWAP.OKX",
        "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL",
    )
    assert type(s).__name__ == "EMACross"
    assert s.config.fast_ema_period == 21
    assert s.config.slow_ema_period == 55
    assert str(s.config.instrument_id) == "BTC-USDT-SWAP.OKX"
    assert str(s.config.bar_type) == "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"


class _Money:
    def __init__(self, v: float):
        self._v = v

    def as_double(self) -> float:
        return self._v


def _pos(closed: bool, ts: int, pnl: float, ret: float):
    return SimpleNamespace(is_closed=closed, ts_closed=ts, realized_pnl=_Money(pnl), realized_return=ret)


def test_extract_filters_warmup_and_open_and_sorts():
    positions = [
        _pos(True, 50, 1.0, 0.001),    # window 시작 전 — 제외 (warmup)
        _pos(False, 150, 9.0, 0.009),  # 미청산 — 제외
        _pos(True, 120, -2.0, -0.002),
        _pos(True, 200, 5.0, 0.005),
    ]
    r = extract_run_result(positions, window_start_ns=100)
    assert r.trade_pnls == [-2.0, 5.0]
    assert r.trade_returns == [-0.002, 0.005]
    assert r.n_trades == 2
    assert r.total_pnl == 3.0


def test_extract_empty():
    r = extract_run_result([], window_start_ns=0)
    assert r.n_trades == 0 and r.trade_pnls == [] and r.total_pnl == 0.0


class _Bal:
    def __init__(self, v: float):
        self.total = _Money(v)


class _Ev:
    def __init__(self, ts: int, bal: float):
        self.ts_init = ts
        self.balances = [_Bal(bal)]


class _Acc:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


class _Cache:
    def __init__(self, account):
        self._account = account

    def account_for_venue(self, venue):
        return self._account


def test_extract_equity_marks_filters_warmup_and_sorts():
    acc = _Acc([_Ev(200, 10050.0), _Ev(50, 10000.0), _Ev(150, 9800.0)])
    marks = extract_equity_marks(_Cache(acc), "OKX", window_start_ns=100)
    assert marks == [(150, 9800.0), (200, 10050.0)]


def test_extract_equity_marks_no_account_or_events():
    assert extract_equity_marks(_Cache(None), "OKX", 0) == []
    assert extract_equity_marks(_Cache(_Acc([])), "OKX", 0) == []
    assert extract_equity_marks(_Cache(_Acc([_Ev(50, 100.0)])), "OKX", 100) == []


def test_extract_analyzer_equity_marks_compounds_daily_returns():
    class Analyzer:
        def portfolio_returns(self):
            return pd.Series(
                [0.10, -0.05, 0.02],
                index=pd.to_datetime([200, 300, 400], unit="ns", utc=True),
            )

    assert extract_analyzer_equity_marks(Analyzer(), 10_000.0, 100) == [
        (100, 10_000.0),
        (200, 11_000.0),
        (300, 10_450.0),
        (400, 10_659.0),
    ]


def test_run_result_default_equity_marks():
    r = RunResult([1.0], [0.01], 1, 1.0)
    assert r.equity_marks == []


def test_extract_fills_filters_warmup_and_signs():
    df = pd.DataFrame({
        "ts_event": [50, 150, 200],
        "order_side": ["BUY", "SELL", "BUY"],
        "last_qty": [1.0, 0.4, 0.2],
        "last_px": [100.0, 101.0, 99.0],
    })
    fills = extract_fills(df, window_start_ns=100)
    assert fills == [(150, -0.4, 101.0), (200, 0.2, 99.0)]


def test_extract_fills_empty():
    assert extract_fills(pd.DataFrame(), 0) == []
    assert extract_fills(None, 0) == []


def test_run_result_default_fills():
    r = RunResult([1.0], [0.01], 1, 1.0)
    assert r.fills == []
