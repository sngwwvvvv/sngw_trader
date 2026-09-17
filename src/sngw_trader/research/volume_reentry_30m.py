"""volume_reentry MR: 5-minute vs 30-minute bar comparison (same TP/SL grid).

Hypothesis: small-timeframe edge is eaten by costs; coarser bars should
trade less and keep more net.
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
    cells = tp_sl_cells()
    for minutes in (5, 30):
        for cell in cells:
            result = run(spec, cell, settings, START, END, rates, bar_minutes=minutes)
            print(
                f"{minutes}m sl={cell['atr_mult']:<4} tp={cell['tp_atr_mult']:<4} "
                f"rt={result['round_trips']:>4} net={result['net']:>8.2f} "
                f"win={result['win_rate']:.2%} sharpe={result['sharpe']:>6.2f} "
                f"mdd={result['mdd']:.4%} car={result['car']:+.4%}"
            )


if __name__ == "__main__":
    main()
