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
    )
    assert settings.instrument_id_str == "ETH-USDT-SWAP.OKX"
    assert settings.is_demo is False
    assert settings.allow_live_orders is False
