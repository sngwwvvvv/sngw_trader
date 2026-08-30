from sngw_trader.config.settings import Settings, load_settings


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
        vol_filter_enabled=True,
        vol_lookback=20,
        vol_threshold=0.80,
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
        vol_filter_enabled=True,
        vol_lookback=20,
        vol_threshold=0.80,
    )
    assert settings.instrument_id_str == "ETH-USDT-SWAP.OKX"
    assert settings.is_demo is False
    assert settings.allow_live_orders is False


def test_err_mom_defaults():
    s = load_settings()
    assert s.strategy in {"err_mom_a", "err_mom_b"}
    assert (s.w_f, s.w_e, s.momentum_window) == (10, 10, 200)
    assert s.ema_fast < s.ema_slow
    assert s.vol_threshold == 0.80
    assert s.trade_size == "0.01"
