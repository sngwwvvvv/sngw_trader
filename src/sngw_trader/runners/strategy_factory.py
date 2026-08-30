"""Attach the configured strategy to either node type. No signal logic here."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.trading import Strategy

from sngw_trader.config.settings import Settings
from sngw_trader.strategies.err_mom_ema30_entry import ErrMomEma30Entry, ErrMomEma30EntryConfig
from sngw_trader.strategies.err_momentum_regime import ErrMomentumRegime, ErrMomentumRegimeConfig


def source_bar_type(instrument_id: str) -> str:
    return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"


def build_err_mom_a(settings: Settings) -> Strategy:
    s = settings
    return ErrMomentumRegime(
        config=ErrMomentumRegimeConfig(
            instrument_id=InstrumentId.from_str(s.instrument_id_str),
            bar_type=BarType.from_str(source_bar_type(s.instrument_id_str)),
            trade_size=Decimal(s.trade_size),
            w_f=s.w_f,
            w_e=s.w_e,
            momentum_window=s.momentum_window,
            theta=s.theta,
            risk_stop_enabled=s.risk_stop_enabled,
            atr_period=s.atr_period,
            atr_mult=s.atr_mult,
            vol_filter_enabled=s.vol_filter_enabled,
            vol_lookback=s.vol_lookback,
            vol_threshold=s.vol_threshold,
        )
    )


def build_err_mom_b(settings: Settings) -> Strategy:
    s = settings
    return ErrMomEma30Entry(
        config=ErrMomEma30EntryConfig(
            instrument_id=InstrumentId.from_str(s.instrument_id_str),
            bar_type=BarType.from_str(source_bar_type(s.instrument_id_str)),
            trade_size=Decimal(s.trade_size),
            w_f=s.w_f,
            w_e=s.w_e,
            momentum_window=s.momentum_window,
            theta=s.theta,
            ema_fast=s.ema_fast,
            ema_slow=s.ema_slow,
            n_pull=s.n_pull,
            risk_stop_enabled=s.risk_stop_enabled,
            atr_period=s.atr_period,
            atr_mult=s.atr_mult,
            vol_filter_enabled=s.vol_filter_enabled,
            vol_lookback=s.vol_lookback,
            vol_threshold=s.vol_threshold,
        )
    )


def build_strategy(settings: Settings) -> Strategy:
    builders = {"err_mom_a": build_err_mom_a, "err_mom_b": build_err_mom_b}
    try:
        return builders[settings.strategy](settings)
    except KeyError as exc:
        raise SystemExit(f"Unknown STRATEGY '{settings.strategy}'. Use err_mom_a or err_mom_b.") from exc
