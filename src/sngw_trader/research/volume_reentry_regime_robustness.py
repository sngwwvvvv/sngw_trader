"""Robustness check for volume_reentry MR 30m regime cell sl=2.0 tp=2.5.

1) Parameter neighborhood: sl {1.8, 2.0, 2.2} x tp {2.2, 2.5, 2.8}, regime on.
2) Monte Carlo bootstrap of the center cell's trade PnLs.
3) Quarterly sub-period runs of the center cell.
"""

from datetime import datetime, timezone
from itertools import product

from sngw_trader.config import load_settings
from sngw_trader.data.funding import fetch_funding_rates_proxy
from sngw_trader.research.bb_mean_reversion_compare import END, SPECS, START, run
from sngw_trader.research.config import MCConfig
from sngw_trader.research.monte_carlo import bootstrap_trades

CENTER = (2.0, 2.5)
QUARTERS = [
    (datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2025, 4, 1, tzinfo=timezone.utc)),
    (datetime(2025, 4, 1, tzinfo=timezone.utc), datetime(2025, 7, 1, tzinfo=timezone.utc)),
    (datetime(2025, 7, 1, tzinfo=timezone.utc), datetime(2025, 10, 1, tzinfo=timezone.utc)),
    (datetime(2025, 10, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)),
]


def main() -> None:
    settings = load_settings()
    inst_id = settings.instrument_id_str.split(".", 1)[0]
    rates = fetch_funding_rates_proxy(
        inst_id, int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    )
    spec = SPECS["volume_reentry"]
    regime_bar_type = (
        f"{settings.instrument_id_str}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )

    def cell(sl: float, tp: float) -> dict:
        return {
            "tp_mode": "atr",
            "hold_across_sessions": True,
            "size_nav_fraction": 1.0,
            "regime_bar_type": regime_bar_type,
            "atr_mult": sl,
            "tp_atr_mult": tp,
        }

    def fmt(r: dict) -> str:
        return (
            f"rt={r['round_trips']:>3} net={r['net']:>8.2f} win={r['win_rate']:.2%} "
            f"sharpe={r['sharpe']:>5.2f} mdd={r['mdd']:.4%} car={r['car']:+.4%}"
        )

    print("== neighborhood (regime on, 30m) ==")
    pnls: list[float] | None = None
    for sl, tp in product((1.8, 2.0, 2.2), (2.2, 2.5, 2.8)):
        r = run(spec, cell(sl, tp), settings, START, END, rates, bar_minutes=30)
        marker = "  <- center" if (sl, tp) == CENTER else ""
        print(f"sl={sl:<4} tp={tp:<4} {fmt(r)}{marker}")
        if (sl, tp) == CENTER:
            pnls = r["trade_pnls"]

    print("== monte carlo bootstrap (center) ==")
    mc = bootstrap_trades(pnls, MCConfig(n_sims=1000, seed=42))
    print(
        f"trades={mc.n_trades} final p5={mc.final_return_p5:+.4%} "
        f"p50={mc.final_return_p50:+.4%} p95={mc.final_return_p95:+.4%} "
        f"worst={mc.worst_final_return:+.4%} ruin={mc.ruin_prob:.2%} "
        f"mdd p50={mc.mdd_p50:.4%} p95={mc.mdd_p95:.4%}"
    )

    print("== quarterly (center) ==")
    for qs, qe in QUARTERS:
        r = run(spec, cell(*CENTER), settings, qs, qe, rates, bar_minutes=30)
        print(f"{qs:%Y-%m-%d} ~ {qe:%Y-%m-%d} {fmt(r)}")


if __name__ == "__main__":
    main()
