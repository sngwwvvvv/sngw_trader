from datetime import datetime, timezone
from pathlib import Path

import pytest

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


class _StubEngine:
    def __init__(self):
        self.added = []

    def add_strategy(self, strategy):
        self.added.append(strategy)


class _StubRunConfig:
    def __init__(self, run_id):
        self.id = run_id


class _StubNode:
    def __init__(self, engine):
        self._engine = engine

    def get_engine(self, run_config_id):
        return self._engine


def test_attach_strategy_goes_to_engine():
    engine = _StubEngine()
    attach_strategy(_StubNode(engine), _StubRunConfig("run-config-id"), "BTC-USDT-SWAP.OKX")
    assert len(engine.added) == 1
    assert engine.added[0].config.fast_ema_period == 10


def test_attach_strategy_raises_without_engine():
    with pytest.raises(RuntimeError):
        attach_strategy(_StubNode(None), _StubRunConfig("run-config-id"), "BTC-USDT-SWAP.OKX")