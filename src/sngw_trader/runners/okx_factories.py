"""Resolve OKX factory symbols across Nautilus versions.

Do not invent a client here. Only import official adapter classes.
"""

from __future__ import annotations

from importlib import import_module


def load_okx_symbols() -> dict[str, object]:
    okx = import_module("nautilus_trader.adapters.okx")
    names = dir(okx)

    def pick(*candidates: str) -> object:
        for name in candidates:
            if name in names:
                return getattr(okx, name)
        raise ImportError(
            "Missing OKX symbol. Tried "
            + ", ".join(candidates)
            + f". Available: {names}"
        )

    return {
        "DataConfig": pick("OKXDataClientConfig"),
        "ExecConfig": pick("OKXExecClientConfig"),
        "DataFactory": pick("OKXLiveDataClientFactory", "OKXDataClientFactory"),
        "ExecFactory": pick(
            "OKXLiveExecClientFactory",
            "OKXExecutionClientFactory",
            "OKXExecClientFactory",
        ),
        "Environment": pick("OKXEnvironment"),
        "InstrumentType": pick("OKXInstrumentType"),
        "MarginMode": pick("OKXMarginMode"),
        "Region": pick("OKXRegion"),
    }


def instrument_type_from_settings(instrument_type_enum: object, raw: str) -> object:
    key = raw.strip().upper()
    aliases = {
        "SWAP": "SWAP",
        "PERP": "SWAP",
        "SPOT": "SPOT",
        "FUTURES": "FUTURES",
        "FUTURE": "FUTURES",
        "OPTION": "OPTION",
        "MARGIN": "MARGIN",
    }
    name = aliases.get(key, key)
    if hasattr(instrument_type_enum, name):
        return getattr(instrument_type_enum, name)
    raise ValueError(f"Unsupported OKX_INSTRUMENT_TYPE={raw}")
