from nautilus_trader.model import Price, Quantity
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import OrderType

from sngw_trader.config import load_settings
from sngw_trader.runners.backtest_models import OkxRateFeeModel, OkxRateFeeModelConfig
from sngw_trader.runners.backtest_okx import build_run_config


class _FakeOrder:
    def __init__(self, order_type: OrderType) -> None:
        self.order_type = order_type


class _FakeInstrument:
    quote_currency = USDT


def test_fee_model_taker_and_maker() -> None:
    model = OkxRateFeeModel(OkxRateFeeModelConfig(maker_fee_rate=0.0002, taker_fee_rate=0.0005))
    taker = model.get_commission(
        _FakeOrder(OrderType.MARKET),
        Quantity.from_int(10),
        Price.from_str("100.0"),
        _FakeInstrument(),
    )
    assert taker.as_double() == 10 * 100.0 * 0.0005
    maker = model.get_commission(
        _FakeOrder(OrderType.LIMIT),
        Quantity.from_int(10),
        Price.from_str("100.0"),
        _FakeInstrument(),
    )
    assert maker.as_double() == 10 * 100.0 * 0.0002


def test_run_config_has_fill_fee_latency() -> None:
    settings = load_settings()
    run_config = build_run_config(str(settings.catalog_path), settings.instrument_id_str, settings)
    venue = run_config.venues[0]
    assert venue.fill_model is not None
    assert venue.fill_model.config.prob_fill_on_limit == settings.bt_prob_fill_on_limit
    assert venue.fill_model.config.prob_slippage == settings.bt_prob_slippage
    assert venue.fee_model is not None
    assert venue.fee_model.config["taker_fee_rate"] == settings.bt_taker_fee
    assert venue.latency_model is not None
    assert venue.latency_model.config.base_latency_nanos == settings.bt_latency_ms * 1_000_000
