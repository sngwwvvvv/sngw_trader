"""S07 walk-forward runner. BacktestNode assembly only — no trading conditions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.trading.config import ImportableStrategyConfig, StrategyFactory

from sngw_trader.runners.backtest_okx import build_multi_instrument_run_config

DEFAULT_CONFIG = {
    "catalog_path": "catalog",
    "instrument_ids": ["ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX"],
    "pair_ids": [1],
    "start": "2024-09-01T00:00:00+00:00",
    "end": "2025-09-01T00:00:00+00:00",
    "taker_fee": 0.0005,
    "maker_fee": 0.0002,
    "cost_multiplier": 1.0,
    "prob_slippage": 0.5,
    "prob_fill_on_limit": 0.9,
    "formation_hours": 720,
    "trading_hours": 240,
    "equity_usdt": 10000.0,
    "funding_dir": "catalog/funding",
    "events_path": "",
    "output_dir": "research/kalman",
}


def build_strategy_config(cfg: dict) -> dict:
    return {
        "strategy_path": "sngw_trader.strategies.kalman_spread:KalmanSpreadStrategy",
        "config_path": "sngw_trader.strategies.kalman_spread:KalmanSpreadConfig",
        "config": {
            "instrument_ids": tuple(cfg["instrument_ids"]),
            "pair_ids": tuple(cfg["pair_ids"]),
            "formation_hours": cfg["formation_hours"],
            "trading_hours": cfg["trading_hours"],
            "equity_usdt": cfg["equity_usdt"],
            "taker_fee": cfg["taker_fee"] * cfg["cost_multiplier"],
            "cost_multiplier": cfg["cost_multiplier"],
            "funding_dir": cfg["funding_dir"],
            "events_path": cfg["events_path"],
        },
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="research/kalman/config.json")
    args = parser.parse_args(argv)
    cfg = {**DEFAULT_CONFIG, **json.loads(Path(args.config).read_text(encoding="utf-8"))}
    run_config = build_multi_instrument_run_config(
        cfg["catalog_path"], cfg["instrument_ids"],
        start=datetime.fromisoformat(cfg["start"]), end=datetime.fromisoformat(cfg["end"]),
        taker_fee=cfg["taker_fee"] * cfg["cost_multiplier"],
        maker_fee=cfg["maker_fee"] * cfg["cost_multiplier"],
        prob_slippage=cfg["prob_slippage"], prob_fill_on_limit=cfg["prob_fill_on_limit"],
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    strategy = StrategyFactory.create(ImportableStrategyConfig(**build_strategy_config(cfg)))
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"Engine not built for run config {run_config.id}")
    engine.add_strategy(strategy)
    try:
        node.run()
        trader = next(e.trader for e in node.get_engines() if hasattr(e, "trader"))
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        trader.generate_order_fills_report().to_csv(out_dir / "fills.csv", index=False)
        results = {
            "config": {k: v for k, v in cfg.items()},
            "n_orders": len(trader.generate_orders_report()),
        }
        (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
        print(f"wrote {out_dir / 'results.json'}")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
