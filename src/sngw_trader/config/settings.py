"""Runtime settings from environment. No secrets in source."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sngw_trader.config.dotenv import load_dotenv


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip()


def _optional_ts(name: str) -> datetime | None:
    raw = _env(name)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"{name} must be ISO8601/YYYY-MM-DD, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    okx_env: str
    confirm_live: str
    trader_id: str
    account_id: str
    node_name: str
    instrument_type: str
    symbol: str
    margin_mode: str
    region: str
    catalog_path: Path
    log_dir: Path
    redis_enabled: bool
    redis_host: str
    redis_port: int
    bt_maker_fee: float
    bt_taker_fee: float
    bt_latency_ms: int
    bt_prob_fill_on_limit: float
    bt_prob_slippage: float
    catalog_start: datetime | None
    catalog_end: datetime | None

    @property
    def is_demo(self) -> bool:
        return self.okx_env.lower() != "live"

    @property
    def allow_live_orders(self) -> bool:
        return (not self.is_demo) and self.confirm_live.upper() == "YES"

    @property
    def instrument_id_str(self) -> str:
        symbol = self.symbol.upper()
        if symbol.endswith(".OKX"):
            return symbol
        return f"{symbol}.OKX"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        okx_env=_env("OKX_ENV", "demo"),
        confirm_live=_env("CONFIRM_LIVE", "NO"),
        trader_id=_env("TRADER_ID", "TRADER-001"),
        account_id=_env("ACCOUNT_ID", "OKX-001"),
        node_name=_env("NODE_NAME", "OKX-NODE-001"),
        instrument_type=_env("OKX_INSTRUMENT_TYPE", "SWAP"),
        symbol=_env("OKX_SYMBOL", "BTC-USDT-SWAP"),
        margin_mode=_env("OKX_MARGIN_MODE", "CROSS"),
        region=_env("OKX_REGION", "GLOBAL"),
        catalog_path=Path(_env("CATALOG_PATH", "./catalog")).resolve(),
        log_dir=Path(_env("LOG_DIR", "./logs")).resolve(),
        redis_enabled=_env("REDIS_ENABLED", "false").lower() in {"1", "true", "yes"},
        redis_host=_env("REDIS_HOST", "127.0.0.1"),
        redis_port=int(_env("REDIS_PORT", "6379")),
        # OKX SWAP standard tier: maker 0.02% / taker 0.05% (spot: 0.08% / 0.10%)
        bt_maker_fee=float(_env("BT_MAKER_FEE", "0.0002")),
        bt_taker_fee=float(_env("BT_TAKER_FEE", "0.0005")),
        bt_latency_ms=int(_env("BT_LATENCY_MS", "200")),
        bt_prob_fill_on_limit=float(_env("BT_PROB_FILL_ON_LIMIT", "0.7")),
        bt_prob_slippage=float(_env("BT_PROB_SLIPPAGE", "0.1")),
        catalog_start=_optional_ts("CATALOG_START"),
        catalog_end=_optional_ts("CATALOG_END"),
    )
