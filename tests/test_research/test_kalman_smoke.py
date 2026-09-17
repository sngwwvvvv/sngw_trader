# tests/test_research/test_kalman_smoke.py
"""E2E smoke: 2 synthetic instruments, ~42 days of 1h mark bars, one pair.

Uses a shrunk formation/trading window (72/48) so full cycles fit the
synthetic window, and forces the screen verdict via monkeypatch (gate logic
is covered by the Task 1 unit tests) so entries are deterministic. The
spread is shocked during the first two trading windows so |z| crosses the
entry band. Asserts the run completes and produces fill pairs.
"""

import math
import random
from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model import Bar, BarType, QuoteTick
from nautilus_trader.model.currencies import BTC, ETH, USDT
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments.crypto_perpetual import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.research.kalman_walkforward import (
    DEFAULT_CONFIG,
    build_multi_instrument_run_config,
    build_strategy_config,
)


def test_build_strategy_config_passes_raw_taker_fee():
    cfg = {**DEFAULT_CONFIG, "taker_fee": 0.0005, "cost_multiplier": 2.0}
    strategy_cfg = build_strategy_config(cfg)["config"]
    assert strategy_cfg["taker_fee"] == 0.0005   # raw; strategy applies cost_multiplier itself
    assert strategy_cfg["cost_multiplier"] == 2.0

IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")
HOUR_MS = 3_600_000
_RAW_SCALE = Decimal(10) ** 9   # Bar.from_raw takes 1e9-scaled fixed-point values


def _okx_swap(inst_id: str) -> CryptoPerpetual:
    symbol = inst_id.split("-")[0]
    base = {"ETH": ETH, "BTC": BTC}[symbol]
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-USDT-SWAP"), Venue("OKX")),
        raw_symbol=Symbol(f"{symbol}USDT"),
        base_currency=base,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("10000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("1000000.00"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("1.00"),
        margin_maint=Decimal("0.35"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        lot_size=Quantity.from_str("0.001"),   # OKX lot; default 1 floors sizing to 0
        ts_event=0,
        ts_init=0,
    )


def _write_synthetic_catalog(catalog: ParquetDataCatalog) -> None:
    for inst_id in IDS:
        catalog.write_data([_okx_swap(inst_id)])
    rng = random.Random(5)
    x = 30_000.0
    e = 0.0
    start_ms = 1_700_000_000_000 - (1_700_000_000_000 % HOUR_MS)
    y_bars, x_bars, quotes = [], [], []
    for h in range(1000):
        shock = 0.03 if (80 <= h < 100 or 200 <= h < 220) else 0.0  # z spikes in trading windows
        x *= math.exp(rng.gauss(0.0, 0.008))
        e = 0.9 * e + rng.gauss(0.0, 0.004) + shock
        y = 2.0 * x * math.exp(e)
        ts = start_ms + h * HOUR_MS
        ns = ts * 1_000_000
        for inst_id, close, bucket in ((IDS[1], x, x_bars), (IDS[0], y, y_bars)):
            bucket.append(Bar.from_raw(
                bar_type=BarType.from_str(f"{inst_id}-1-HOUR-MARK-EXTERNAL"),
                open=Decimal(str(close)) * _RAW_SCALE,
                high=Decimal(str(close)) * _RAW_SCALE,
                low=Decimal(str(close)) * _RAW_SCALE,
                close=Decimal(str(close)) * _RAW_SCALE,
                price_prec=2, volume=Decimal("100") * _RAW_SCALE, size_prec=3,
                ts_event=ns, ts_init=ns,
            ))
            # MARK bars are rejected by the matching engine (nautilus 1.231),
            # so the venue book is driven by quote ticks instead.
            iid = InstrumentId.from_str(inst_id)
            quotes.append(QuoteTick(
                iid,
                Price(Decimal(str(close)), 2), Price(Decimal(str(close)), 2),
                Quantity(Decimal("100"), 3), Quantity(Decimal("100"), 3),
                ns, ns,
            ))
    catalog.write_data(x_bars + y_bars, data_cls=Bar)
    catalog.write_data(quotes, data_cls=QuoteTick)


def test_smoke_run_completes_with_output(tmp_path, monkeypatch):
    from nautilus_trader.backtest.node import BacktestNode
    from nautilus_trader.config import BacktestDataConfig
    from nautilus_trader.trading.config import ImportableStrategyConfig, StrategyFactory

    from sngw_trader.indicators import pair_screening as ps
    import sngw_trader.strategies.kalman_spread as strategy_module

    # Deterministic screen verdict; gate math is unit-tested in Task 1.
    monkeypatch.setattr(strategy_module, "screen_pair",
                        lambda *a, **k: ps.ScreenDecision(True, 80, ()))
    catalog = ParquetDataCatalog(tmp_path / "catalog")
    _write_synthetic_catalog(catalog)
    cfg = {
        **DEFAULT_CONFIG,
        "catalog_path": str(tmp_path / "catalog"),
        "instrument_ids": list(IDS),
        "pair_ids": [1],
        "formation_hours": 72,
        "trading_hours": 48,
        "funding_dir": "",
        "events_path": "",
        "output_dir": str(tmp_path / "out"),
        "start": "2023-11-14T22:00:00+00:00",
        "end": "2023-12-27T00:00:00+00:00",
    }
    run_config = build_multi_instrument_run_config(
        cfg["catalog_path"], cfg["instrument_ids"],
        start=datetime(2023, 11, 14, 22, tzinfo=timezone.utc),
        end=datetime(2023, 12, 27, tzinfo=timezone.utc),
        taker_fee=0.0005, maker_fee=0.0002,
        prob_slippage=0.5, prob_fill_on_limit=0.9,
        bar_execution=False,
        dispose_on_completion=False,   # fills report is read after run()
    )
    for iid in IDS:   # quote feed drives the venue book (bar_execution=False)
        run_config.data.append(BacktestDataConfig(
            data_cls=QuoteTick, catalog_path=cfg["catalog_path"], instrument_id=iid,
            start_time=datetime(2023, 11, 14, 22, tzinfo=timezone.utc).isoformat(),
            end_time=datetime(2023, 12, 27, tzinfo=timezone.utc).isoformat(),
        ))
    node = BacktestNode(configs=[run_config])
    node.build()
    # Instance-based add (executor pattern); node.add_strategy(id, ImportableStrategyConfig)
    # does not work on nautilus 1.231.0.
    strategy = StrategyFactory.create(ImportableStrategyConfig(**build_strategy_config(cfg)))
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"Engine not built for run config {run_config.id}")
    engine.add_strategy(strategy)
    try:
        node.run()
        trader = next(e.trader for e in node.get_engines() if hasattr(e, "trader"))
        fills = trader.generate_order_fills_report()
        assert len(fills) >= 2, "expected at least one entry fill pair"
    finally:
        node.dispose()
