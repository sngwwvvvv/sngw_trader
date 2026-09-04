from datetime import datetime

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings


def test_instrument_id_appends_venue() -> None:
    settings = Settings(
        okx_env="demo",
        confirm_live="NO",
        trader_id="TRADER-001",
        account_id="OKX-001",
        node_name="OKX-NODE-001",
        instrument_type="SWAP",
        symbol="BTC-USDT-SWAP",
        margin_mode="CROSS",
        region="GLOBAL",
        catalog_path=__import__("pathlib").Path("catalog"),
        log_dir=__import__("pathlib").Path("logs"),
        redis_enabled=False,
        redis_host="127.0.0.1",
        redis_port=6379,
        bt_maker_fee=0.0002,
        bt_taker_fee=0.0005,
        bt_latency_ms=100,
        bt_prob_fill_on_limit=0.7,
        bt_prob_slippage=0.1,
        strategy="err_mom_a",
        w_f=10,
        w_e=10,
        momentum_window=200,
        theta=0.0,
        ema_fast=20,
        ema_slow=50,
        n_pull=24,
        trade_size="0.01",
        risk_stop_enabled=True,
        atr_period=14,
        atr_mult=3.0,
        sizing_mode="vol_target",
        size_target_vol=0.20,
        size_half_life=20,
        size_min_scale=0.0,
        size_max_scale=3.0,
        size_rebalance_band=0.10,
        catalog_start=datetime(2026, 1, 1),
        catalog_end=datetime(2026, 3, 1),
    )
    assert settings.instrument_id_str == "BTC-USDT-SWAP.OKX"
    assert settings.is_demo is True
    assert settings.allow_live_orders is False


def test_live_requires_confirm() -> None:
    settings = Settings(
        okx_env="live",
        confirm_live="NO",
        trader_id="TRADER-001",
        account_id="OKX-001",
        node_name="OKX-NODE-001",
        instrument_type="SWAP",
        symbol="ETH-USDT-SWAP.OKX",
        margin_mode="CROSS",
        region="GLOBAL",
        catalog_path=__import__("pathlib").Path("catalog"),
        log_dir=__import__("pathlib").Path("logs"),
        redis_enabled=False,
        redis_host="127.0.0.1",
        redis_port=6379,
        bt_maker_fee=0.0002,
        bt_taker_fee=0.0005,
        bt_latency_ms=100,
        bt_prob_fill_on_limit=0.7,
        bt_prob_slippage=0.1,
        strategy="err_mom_a",
        w_f=10,
        w_e=10,
        momentum_window=200,
        theta=0.0,
        ema_fast=20,
        ema_slow=50,
        n_pull=24,
        trade_size="0.01",
        risk_stop_enabled=True,
        atr_period=14,
        atr_mult=3.0,
        sizing_mode="vol_target",
        size_target_vol=0.20,
        size_half_life=20,
        size_min_scale=0.0,
        size_max_scale=3.0,
        size_rebalance_band=0.10,
        catalog_start=datetime(2026, 1, 1),
        catalog_end=datetime(2026, 3, 1),
    )
    assert settings.instrument_id_str == "ETH-USDT-SWAP.OKX"
    assert settings.is_demo is False
    assert settings.allow_live_orders is False


def test_err_mom_defaults():
    s = load_settings()
    assert s.strategy in {"err_mom_a", "err_mom_b"}
    assert (s.w_f, s.w_e, s.momentum_window) == (10, 10, 200)
    assert s.ema_fast < s.ema_slow
    assert s.sizing_mode == "vol_target"
    assert s.size_target_vol == 0.20
    assert s.size_half_life == 20
    assert s.size_rebalance_band == 0.10
    assert s.trade_size == "0.01"


def test_bad_sizing_mode_exits(monkeypatch):
    import pytest

    monkeypatch.setenv("SIZING_MODE", "nope")
    with pytest.raises(SystemExit):
        load_settings()


def _base_env() -> dict[str, str]:
    return {
        "OKX_ENV": "demo",
        "CATALOG_START": "2026-01-01",
        "CATALOG_END": "2026-03-01",
    }


def test_load_settings_parses_catalog_range(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert s.catalog_start == datetime(2026, 1, 1)
    assert s.catalog_end == datetime(2026, 3, 1)


def test_load_settings_without_catalog_range_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("OKX_ENV", "demo")
    s = load_settings()
    assert s.catalog_start is None
    assert s.catalog_end is None


def test_load_settings_rejects_malformed_catalog_start(monkeypatch) -> None:
    monkeypatch.setenv("OKX_ENV", "demo")
    monkeypatch.setenv("CATALOG_START", "not-a-date")
    import pytest
    with pytest.raises(SystemExit):
        load_settings()


def test_download_bars_rejects_inverted_range(monkeypatch) -> None:
    import pytest

    from sngw_trader.data.catalog_writer import _validate_range

    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CATALOG_START", "2026-03-01")
    monkeypatch.setenv("CATALOG_END", "2026-01-01")
    s = load_settings()
    with pytest.raises(SystemExit):
        _validate_range(s)