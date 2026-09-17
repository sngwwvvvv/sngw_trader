from datetime import datetime, timezone

from sngw_trader.runners.backtest_okx import build_multi_instrument_run_config

IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")


def test_multi_instrument_run_config_shape():
    cfg = build_multi_instrument_run_config(
        "catalog", IDS,
        start=datetime(2024, 9, 1, tzinfo=timezone.utc),
        end=datetime(2024, 10, 1, tzinfo=timezone.utc),
        taker_fee=0.0005, maker_fee=0.0002,
        prob_slippage=0.5, prob_fill_on_limit=0.9,
    )
    assert len(cfg.data) == 2
    assert cfg.venues[0].name == "OKX"
    assert cfg.venues[0].fee_model.config["taker_fee_rate"] == 0.0005
    assert cfg.venues[0].fill_model.config.prob_slippage == 0.5
    assert cfg.venues[0].fill_model.config.prob_fill_on_limit == 0.9
