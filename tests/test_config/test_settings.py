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
        catalog_start=datetime(2026, 1, 1),
        catalog_end=datetime(2026, 3, 1),
    )
    assert settings.instrument_id_str == "ETH-USDT-SWAP.OKX"
    assert settings.is_demo is False
    assert settings.allow_live_orders is False


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


def test_load_settings_requires_catalog_start(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CATALOG_START")
    import pytest
    with pytest.raises(SystemExit):
        load_settings()
