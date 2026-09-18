"""Walk-forward for volume_reentry MR 30m regime cell sl=2.0 tp=2.5.

Fixed params (no IS re-selection), regime filter on, NAV 1.0.
Rolling windows: IS = 6 months, OOS = 3 months, shifted by 3 months.
IS metrics are informational only (params never change between windows).
"""

from datetime import datetime, timezone

from sngw_trader.config import load_settings
from sngw_trader.data.funding import fetch_funding_rates_proxy
from sngw_trader.research.bb_mean_reversion_compare import SPECS, run

CENTER = (2.0, 2.5)
WF_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
WF_END = datetime(2026, 1, 1, tzinfo=timezone.utc)
IS_MONTHS = 6
OOS_MONTHS = 3


def _add_months(dt: datetime, n: int) -> datetime:
    month = dt.month - 1 + n
    return dt.replace(year=dt.year + month // 12, month=month % 12 + 1)


def main() -> None:
    settings = load_settings()
    inst_id = settings.instrument_id_str.split(".", 1)[0]
    rates = fetch_funding_rates_proxy(
        inst_id,
        int(WF_START.timestamp() * 1000),
        int(WF_END.timestamp() * 1000),
    )
    print(f"funding points (Binance proxy): {len(rates)}")

    spec = SPECS["volume_reentry"]
    regime_bar_type = (
        f"{settings.instrument_id_str}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    cell = {
        "tp_mode": "atr",
        "hold_across_sessions": True,
        "size_nav_fraction": 1.0,
        "regime_bar_type": regime_bar_type,
        "atr_mult": CENTER[0],
        "tp_atr_mult": CENTER[1],
    }

    def fmt(r: dict) -> str:
        def pct(value: float | None, signed: bool = False) -> str:
            if value is None:
                return "n/a".rjust(9 if not signed else 10)
            return f"{value:+.2%}" if signed else f"{value:.2%}".rjust(7)

        def num(value: float | None, width: int = 5, precision: int = 2) -> str:
            return "n/a".rjust(width) if value is None else f"{value:>{width}.{precision}f}"

        return (
            f"rt={r['round_trips']:>3} net={r['net']:>10.2f} "
            f"win={pct(r['win_rate'])} sharpe={num(r['sharpe'])} "
            f"mdd={pct(r['mdd'])} car={pct(r['car'], signed=True)}"
        )

    oos_rows: list[tuple[datetime, datetime, dict]] = []
    oos_start = _add_months(WF_START, IS_MONTHS)
    while _add_months(oos_start, OOS_MONTHS) <= WF_END:
        is_start = _add_months(oos_start, -IS_MONTHS)
        oos_end = _add_months(oos_start, OOS_MONTHS)
        # Clip funding to each window: open positions at window end must not
        # accrue funding past the engine's last fill.
        is_rates = {
            ts: v
            for ts, v in rates.items()
            if int(is_start.timestamp() * 1000) <= ts < int(oos_start.timestamp() * 1000)
        }
        oos_rates = {
            ts: v
            for ts, v in rates.items()
            if int(oos_start.timestamp() * 1000) <= ts < int(oos_end.timestamp() * 1000)
        }
        is_result = run(spec, cell, settings, is_start, oos_start, is_rates, bar_minutes=30)
        oos_result = run(spec, cell, settings, oos_start, oos_end, oos_rates, bar_minutes=30)
        oos_rows.append((oos_start, oos_end, oos_result))
        print(
            f"OOS {oos_start:%Y-%m} ~ {oos_end:%Y-%m} {fmt(oos_result)} | "
            f"IS {is_start:%Y-%m} ~ {oos_start:%Y-%m} {fmt(is_result)}"
        )
        oos_start = _add_months(oos_start, OOS_MONTHS)

    cars = [r["car"] for _, _, r in oos_rows if r["car"] is not None]
    if cars:
        positives = sum(1 for c in cars if c > 0)
        print(
            f"\nOOS windows: {len(oos_rows)} | positive: {positives}/{len(cars)} | "
            f"median car: {sorted(cars)[len(cars) // 2]:+.4%}"
        )


if __name__ == "__main__":
    main()
