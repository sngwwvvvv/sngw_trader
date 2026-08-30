from types import SimpleNamespace

from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy, extract_run_result

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
