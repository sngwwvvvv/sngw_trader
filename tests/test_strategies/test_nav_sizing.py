from decimal import Decimal
from types import SimpleNamespace

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.model.objects import Money

from sngw_trader.strategies.err_momentum_regime import (
    ErrMomentumRegime,
    ErrMomentumRegimeConfig,
)
from sngw_trader.strategies.sizing import NavFractionMixin

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Currency


def _money(amount: str, code: str = "USDT") -> Money:
    return Money(amount, Currency.from_str(code))


class _StubStrategy(NavFractionMixin):
    def __init__(self, config, instrument=None, account=None):
        self.config = config
        self.cache = SimpleNamespace(instrument=lambda _id: instrument)
        self.portfolio = SimpleNamespace(account=lambda _venue: account)


def _config(**overrides):
    kwargs = dict(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        trade_size=Decimal("0.01"),
        size_nav_fraction=0.0,
    )
    kwargs.update(overrides)
    return SimpleNamespace(**kwargs)


def _instrument():
    return SimpleNamespace(
        id=SimpleNamespace(venue="OKX"),
        quote_currency="USDT",
    )


def test_zero_fraction_falls_back_to_trade_size():
    s = _StubStrategy(_config())
    assert s.unit_qty(_config(), Decimal("100")) == Decimal("0.01")


def test_fraction_none_price_returns_none():
    s = _StubStrategy(_config(size_nav_fraction=1.0))
    assert s.unit_qty(_config(size_nav_fraction=1.0), None) is None
    assert s.unit_qty(_config(size_nav_fraction=1.0), Decimal("0")) is None


def test_fraction_nav_over_price():
    cfg = _config(size_nav_fraction=1.0)
    account = SimpleNamespace(balance_total=lambda _c: _money("10000"))
    s = _StubStrategy(cfg, instrument=_instrument(), account=account)
    unit = s.unit_qty(cfg, Decimal("100"))
    assert unit == Decimal("10000") / Decimal("100")


def test_fraction_multiplied():
    cfg = _config(size_nav_fraction=2.0)
    account = SimpleNamespace(balance_total=lambda _c: _money("10000"))
    s = _StubStrategy(cfg, instrument=_instrument(), account=account)
    assert s.unit_qty(cfg, Decimal("50")) == Decimal("400")


def test_missing_instrument_or_account_returns_none():
    cfg = _config(size_nav_fraction=1.0)
    s = _StubStrategy(cfg, instrument=None, account=None)
    assert s.unit_qty(cfg, Decimal("100")) is None
    s2 = _StubStrategy(cfg, instrument=_instrument(), account=None)
    assert s2.unit_qty(cfg, Decimal("100")) is None


def test_missing_balance_returns_none():
    cfg = _config(size_nav_fraction=1.0)
    account = SimpleNamespace(balance_total=lambda _c: None)
    s = _StubStrategy(cfg, instrument=_instrument(), account=account)
    assert s.unit_qty(cfg, Decimal("100")) is None


def test_err_mom_sync_uses_nav_unit():
    config = ErrMomentumRegimeConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
        sizing_mode="fixed",
        size_nav_fraction=1.0,
    )
    s = ErrMomentumRegime(config=config)
    s.unit_qty = lambda cfg, px: Decimal("2")  # stub: NAV unit = 2 contracts
    s._signed_qty = lambda: Decimal("0")
    submitted = []
    s._submit = lambda side, qty, seed_stop: submitted.append((side, qty))
    s._sync_size(1, allow_new=True, allow_resize=False)
    assert submitted == [(OrderSide.BUY, Decimal("2"))]
