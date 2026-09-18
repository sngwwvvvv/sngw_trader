"""volume_reentry MR 30m: base vs HTF regime filter (same TP/SL grid).

Regime filter: 4H EMA(20). 4H close >= EMA -> long-only, else short-only.
Blocks new setups only; pending setups and open positions keep TP/SL logic.
"""

from sngw_trader.config import load_settings
from sngw_trader.data.funding import fetch_funding_rates_proxy
from sngw_trader.research.bb_mean_reversion_compare import (
    END,
    SPECS,
    START,
    run,
    tp_sl_cells,
)


def main() -> None:
    settings = load_settings()
    inst_id = settings.instrument_id_str.split(".", 1)[0]
    rates = fetch_funding_rates_proxy(
        inst_id, int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    )
    print(f"funding points (Binance proxy): {len(rates)}")

    spec = SPECS["volume_reentry"]
    regime_bar_type = (
        f"{settings.instrument_id_str}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    for cell in tp_sl_cells():
        rows = {}
        for label, params in (
            ("base", {"size_nav_fraction": 1.0}),
            ("regime", {"regime_bar_type": regime_bar_type, "size_nav_fraction": 1.0}),
        ):
            result = run(
                spec, {**cell, **params}, settings, START, END, rates, bar_minutes=30
            )
            rows[label] = result
        base = rows["base"]
        filt = rows["regime"]
        print(
            f"sl={cell['atr_mult']:<4} tp={cell['tp_atr_mult']:<4} | "
            f"base rt={base['round_trips']:>4} net={base['net']:>8.2f} "
            f"win={base['win_rate']:.2%} sharpe={base['sharpe']:>6.2f} "
            f"mdd={base['mdd']:.4%} car={base['car']:+.4%} | "
            f"regime rt={filt['round_trips']:>4} net={filt['net']:>8.2f} "
            f"win={filt['win_rate']:.2%} sharpe={filt['sharpe']:>6.2f} "
            f"mdd={filt['mdd']:.4%} car={filt['car']:+.4%}"
        )


if __name__ == "__main__":
    main()
