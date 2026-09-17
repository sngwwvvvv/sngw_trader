"""NAV-fraction unit sizing shared by strategies.

size_nav_fraction > 0: unit notional = account equity * fraction,
unit qty = notional / last price. Otherwise the legacy fixed
config.trade_size contract count is used.
"""

from __future__ import annotations

from decimal import Decimal


class NavFractionMixin:
    def unit_qty(self, config, last_price) -> Decimal | None:
        """Unit quantity for one target-vol unit of exposure, or None to skip."""
        frac = Decimal(str(getattr(config, "size_nav_fraction", 0.0)))
        if frac <= 0:
            return config.trade_size
        if last_price is None or last_price <= 0:
            return None
        price = last_price if isinstance(last_price, Decimal) else Decimal(str(last_price))
        instrument = self.cache.instrument(config.instrument_id)
        if instrument is None:
            return None
        account = self.portfolio.account(instrument.id.venue)
        if account is None:
            return None
        balance = account.balance_total(instrument.quote_currency)
        if balance is None:
            return None
        return balance.as_decimal() * frac / price
