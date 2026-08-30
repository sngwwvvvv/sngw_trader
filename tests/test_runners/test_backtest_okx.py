from datetime import datetime, timezone
from pathlib import Path

from sngw_trader.config.settings import Settings
from sngw_trader.runners.backtest_okx import attach_strategy, build_run_config


def _settings() -> Settings:
    return Settings(
        okx_env="demo", confirm_live="NO", trader_id="TRADER-001", account_id="OKX-001",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP", margin_mode="CROSS",
        region="GLOBAL", catalog_path=Path("./catalog"), log_dir=Path("./logs"),
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0002, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
        strategy="err_mom_a", w_f=10, w_e=10, momentum_window=200, theta=0.0,
        ema_fast=20, ema_slow=50, n_pull=24, trade_size="0.01",
        risk_stop_enabled=True, atr_period=14, atr_mult=3.0,
        vol_filter_enabled=True, vol_lookback=20, vol_threshold=0.80,
        catalog_start=datetime(2026, 1, 1), catalog_end=datetime(2026, 3, 1),
    )


def test_build_run_config_with_window():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 7, 1, tzinfo=timezone.utc)
    cfg = build_run_config("catalog", "BTC-USDT-SWAP.OKX", _settings(), start=start, end=end,
                           dispose_on_completion=False, raise_exception=True)
    assert cfg.data[0].start_time == start.isoformat()
    assert cfg.data[0].end_time == end.isoformat()
    assert cfg.dispose_on_completion is False
    assert cfg.raise_exception is True


def test_build_run_config_defaults_unchanged():
    cfg = build_run_config("catalog", "BTC-USDT-SWAP.OKX", _settings())
    assert cfg.data[0].start_time is None
    assert cfg.dispose_on_completion is True
    assert cfg.raise_exception is False


class _StubNode:
    def __init__(self):
        self.added = []

    def add_strategy(self, *args):
        strategy = args[0] if args else None
        self.added.append(strategy)


def test_attach_strategy_builds_configured_strategy():
    node = _StubNode()
    attach_strategy(node, "run-config-id", _settings())
    assert len(node.added) == 1
    assert node.added[0].config.instrument_id.value == "BTC-USDT-SWAP.OKX"


def test_attach_strategy_no_compatible_method_raises():
    class _NoAdd:
        pass

    try:
        attach_strategy(_NoAdd(), "run-config-id", _settings())
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass