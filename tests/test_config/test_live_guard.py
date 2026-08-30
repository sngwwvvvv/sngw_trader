import pytest

from sngw_trader.config.settings import Settings
from sngw_trader.runners.live_okx import assert_safe_mode


def _settings(**kwargs) -> Settings:
    base = dict(
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
    )
    base.update(kwargs)
    return Settings(**base)


def test_demo_is_allowed() -> None:
    assert_safe_mode(_settings(okx_env="demo"))


def test_live_without_confirm_is_blocked() -> None:
    with pytest.raises(SystemExit):
        assert_safe_mode(_settings(okx_env="live", confirm_live="NO"))


def test_live_with_confirm_is_allowed() -> None:
    assert_safe_mode(_settings(okx_env="live", confirm_live="YES"))
