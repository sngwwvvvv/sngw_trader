from decimal import Decimal
from unittest.mock import PropertyMock, patch

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide

from sngw_trader.indicators.bar_aggregator import CompletedBar
from sngw_trader.strategies.err_mom_ema30_entry import (
    ErrMomEma30Entry,
    ErrMomEma30EntryConfig,
    RibbonEntryMachine,
    should_exit,
)

EF, ES = 105.0, 100.0


def _make_strategy(**overrides):
    config = ErrMomEma30EntryConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
        **overrides,
    )
    return ErrMomEma30Entry(config=config)


def test_regime_sign_flip_resets_opposite_machine():
    s = _make_strategy()
    long = s._prepare_machine(1)
    assert s._long.state == "IDLE"
    assert long.update(o=106, h=111, l=105, c=110, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert s._long.state == "EXT"
    short = s._prepare_machine(-1)
    assert short is s._short
    assert s._long.state == "IDLE"
    long2 = s._prepare_machine(1)
    assert long2 is s._long
    assert s._long.state == "IDLE"


def test_long_fsm_full_path():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert m.state == "EXT"
    assert m.update(o=110, h=110, l=104, c=108, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=106, h=110, l=104.5, c=106, ema_fast=EF, ema_slow=ES, regime_allows=True) is True
    assert m.state == "TRIG"


def test_first_bar_in_ext_cannot_touch_into_pull():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert m.update(o=104, h=105, l=104, c=104, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"


def test_band_start_without_ext_never_triggers():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    for c in (104.0, 104.5, 104.0, 103.0):
        assert m.update(o=104, h=107, l=103, c=c, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "IDLE"


def test_regime_loss_resets_setup():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True)
    assert m.state == "EXT"
    m.update(o=106, h=111, l=104, c=106, ema_fast=105, ema_slow=100, regime_allows=False)
    assert m.state == "IDLE"


def test_pull_timeout_resets():
    m = RibbonEntryMachine(direction=1, n_pull=1)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.update(o=104, h=105, l=102, c=104, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=104, h=105, l=103, c=104.5, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "IDLE"


def test_ribbon_break_resets():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True)
    m.update(o=106, h=107, l=98, c=99, ema_fast=105, ema_slow=100, regime_allows=True)
    assert m.state == "IDLE"


def test_short_mirror():
    m = RibbonEntryMachine(direction=-1, n_pull=3)
    assert m.update(o=94, h=95, l=89, c=90, ema_fast=95, ema_slow=100, regime_allows=True) is False
    assert m.state == "EXT"
    assert m.update(o=90, h=96, c=92, l=89, ema_fast=95, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=94, h=95.5, l=90, c=94, ema_fast=95, ema_slow=100, regime_allows=True) is True
    assert m.state == "TRIG"


def test_should_exit_priority():
    assert should_exit(1, 0, close=120, ema_fast=105, ema_slow=100) is True
    assert should_exit(1, 1, close=99, ema_fast=105, ema_slow=100) is True
    assert should_exit(1, 1, close=106, ema_fast=100, ema_slow=105) is True
    assert should_exit(1, 1, close=106, ema_fast=105, ema_slow=100) is False
    assert should_exit(-1, 1, close=90, ema_fast=95, ema_slow=100) is True
    assert should_exit(-1, -1, close=95, ema_fast=95, ema_slow=100) is False


def test_acceptance_no_entry_without_regime():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    for i, c in enumerate([110, 111, 104, 104.2, 106]):
        assert m.update(o=105, h=111, l=100 + i * 0.1, c=c, ema_fast=105, ema_slow=100, regime_allows=False) is False


def test_config_builds():
    config = ErrMomEma30EntryConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
    )
    strategy = ErrMomEma30Entry(config=config)
    assert strategy.config.n_pull == 24
    assert strategy.config.ema_fast < strategy.config.ema_slow


# IDLE->EXT->PULL->TRIG short sequence with ema_fast=98, ema_slow=100 seeded.
_SHORT_BARS = (
    (98.0, 99.0, 96.0, 97.0),  # EXT
    (98.5, 98.5, 97.5, 98.0),  # PULL (high in [ema_fast, ema_slow])
    (98.0, 98.5, 96.5, 97.0),  # TRIG
)


def test_allow_short_grid():
    for allow_short, expected in ((False, []), (True, [OrderSide.SELL])):
        s = _make_strategy(allow_short=allow_short)
        s._ermom._value = -0.5
        s._ema_fast._value = 98.0
        s._ema_slow._value = 100.0
        calls = []
        with patch.object(type(s), "portfolio", new_callable=PropertyMock) as pf, \
                patch.object(s, "_submit", side_effect=lambda side: calls.append(side)):
            pf.return_value.is_flat.return_value = True
            for o, h, l, c in _SHORT_BARS:
                s._on_entry(CompletedBar(ts_open_ns=0, ts_close_ns=1, open=o, high=h, low=l, close=c))
        assert calls == expected
