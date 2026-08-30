"""Assemble TradingNode + official OKX adapter. No signal logic."""

from __future__ import annotations

import sys

from nautilus_trader.common import Environment
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model import AccountId, TraderId

from sngw_trader.config import Settings, load_settings
from sngw_trader.runners.okx_factories import instrument_type_from_settings, load_okx_symbols
from sngw_trader.runners.strategy_factory import build_strategy


def assert_safe_mode(settings: Settings) -> None:
    if settings.is_demo:
        return
    if settings.allow_live_orders:
        return
    raise SystemExit(
        "Refusing live OKX execution. Set OKX_ENV=demo, "
        "or set OKX_ENV=live and CONFIRM_LIVE=YES."
    )


def build_node(settings: Settings) -> TradingNode:
    okx = load_okx_symbols()
    environment = okx["Environment"].DEMO if settings.is_demo else okx["Environment"].LIVE
    instrument_type = instrument_type_from_settings(
        okx["InstrumentType"],
        settings.instrument_type,
    )
    region = getattr(okx["Region"], settings.region.upper())
    margin_mode = getattr(okx["MarginMode"], settings.margin_mode.upper())

    data_config = okx["DataConfig"](
        environment=environment,
        instrument_types=[instrument_type],
        region=region,
    )
    exec_config_kwargs = {
        "environment": environment,
        "instrument_types": [instrument_type],
        "region": region,
        "margin_mode": margin_mode,
    }
    # Some versions also require trader/account ids on exec config.
    try:
        exec_config = okx["ExecConfig"](
            trader_id=TraderId.from_str(settings.trader_id),
            account_id=AccountId.from_str(settings.account_id),
            **exec_config_kwargs,
        )
    except TypeError:
        exec_config = okx["ExecConfig"](**exec_config_kwargs)

    cache_config = None
    load_state = False
    save_state = False
    if settings.redis_enabled:
        from nautilus_trader.cache.config import CacheConfig
        from nautilus_trader.common.config import DatabaseConfig

        cache_config = CacheConfig(
            database=DatabaseConfig(
                type="redis",
                host=settings.redis_host,
                port=settings.redis_port,
            )
        )
        load_state = True
        save_state = True

    config = TradingNodeConfig(
        trader_id=TraderId.from_str(settings.trader_id),
        data_clients={"OKX": data_config},
        exec_clients={"OKX": exec_config},
        cache=cache_config,
        load_state=load_state,
        save_state=save_state,
    )

    node = TradingNode(config=config)
    node.add_data_client_factory("OKX", okx["DataFactory"])
    node.add_exec_client_factory("OKX", okx["ExecFactory"])
    node.build()
    return node


def attach_strategy(node: TradingNode, settings: Settings) -> None:
    node.trader.add_strategy(build_strategy(settings))


def main() -> None:
    settings = load_settings()
    assert_safe_mode(settings)
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Starting {settings.node_name} "
        f"env={settings.okx_env} instrument={settings.instrument_id_str}",
        flush=True,
    )
    node = build_node(settings)
    attach_strategy(node, settings)
    try:
        node.run()
    finally:
        if hasattr(node, "dispose"):
            node.dispose()


if __name__ == "__main__":
    try:
        main()
    except ImportError as exc:
        print(f"UNVERIFIED / missing Nautilus symbol: {exc}", file=sys.stderr)
        raise
