"""Exit-combo walk-forward comparison for volume_reentry MR.

Entry is fixed at the selected WF cell (sl=2.0 tp=2.5 ATR, regime on,
hold across sessions). Exits are swapped in stages:
  A baseline -> + regime_exit_buffer {0.0, 0.25, 0.5}
  B winner   -> + chandelier_k {3.5, 4.0}
  C winner   -> + time_stop_bars {8, 16, 24}
Each stage is selected on IS aggregates only; OOS is reported alongside
and summarized for the final path. Rolling windows: IS 6 months,
OOS 3 months, 3-month shift, 2020-01 ~ 2026-01.
"""

from datetime import datetime, timezone

from sngw_trader.config import load_settings
from sngw_trader.data.funding import fetch_funding_rates_proxy
from sngw_trader.research.bb_mean_reversion_compare import SPECS, run

WF_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
WF_END = datetime(2026, 1, 1, tzinfo=timezone.utc)
IS_MONTHS = 6
OOS_MONTHS = 3

BASE_CELL = {
    "tp_mode": "atr",
    "hold_across_sessions": True,
    "size_nav_fraction": 1.0,
    "regime_bar_type": None,  # filled in main()
    "atr_mult": 2.0,
    "tp_atr_mult": 2.5,
}


def _add_months(dt: datetime, n: int) -> datetime:
    month = dt.month - 1 + n
    return dt.replace(year=dt.year + month // 12, month=month % 12 + 1)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _aggregate(rows: list[dict]) -> dict:
    cars = [r["car"] for r in rows if r["car"] is not None]
    sharpes = [r["sharpe"] for r in rows if r["sharpe"] is not None]
    win_rates = [r["win_rate"] for r in rows if r["win_rate"] is not None]
    return {
        "windows": len(rows),
        "positive": sum(1 for c in cars if c > 0),
        "median_car": _median(cars),
        "median_sharpe": _median(sharpes),
        "sum_net": sum(r["net"] for r in rows if r["net"] is not None),
        "trades": sum(r["round_trips"] for r in rows),
        "avg_win": sum(win_rates) / len(win_rates) if win_rates else None,
    }


def _fmt_agg(tag: str, agg: dict) -> str:
    car = "n/a" if agg["median_car"] is None else f"{agg['median_car']:+.2%}"
    sharpe = "n/a" if agg["median_sharpe"] is None else f"{agg['median_sharpe']:.2f}"
    win = "n/a" if agg["avg_win"] is None else f"{agg['avg_win']:.1%}"
    return (
        f"{tag:<22} {agg['positive']:>2}/{agg['windows']:<2} "
        f"car={car:>7} sharpe={sharpe:>5} net={agg['sum_net']:>11.2f} "
        f"trades={agg['trades']:>4} win={win:>5}"
    )


def _run_stage(
    title: str,
    candidates: dict[str, dict],
    windows: list[tuple[datetime, datetime, datetime]],
    rates: dict[int, float],
    spec,
    settings,
) -> tuple[dict[str, dict], dict[str, dict]]:
    print(title)
    is_aggs: dict[str, dict] = {}
    oos_aggs: dict[str, dict] = {}
    for name, cell in candidates.items():
        is_rows: list[dict] = []
        oos_rows: list[dict] = []
        for is_start, oos_start, oos_end in windows:
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
            is_rows.append(
                run(spec, cell, settings, is_start, oos_start, is_rates, bar_minutes=30)
            )
            oos_rows.append(
                run(spec, cell, settings, oos_start, oos_end, oos_rates, bar_minutes=30)
            )
        is_aggs[name] = _aggregate(is_rows)
        oos_aggs[name] = _aggregate(oos_rows)
        print(f"  {_fmt_agg(f'IS  {name}', is_aggs[name])}")
        print(f"  {_fmt_agg(f'OOS {name}', oos_aggs[name])}")
    print()
    return is_aggs, oos_aggs


def _pick(is_aggs: dict[str, dict]) -> str:
    return max(
        is_aggs, key=lambda name: is_aggs[name]["median_car"] or float("-inf")
    )


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
    cell_base = {
        **BASE_CELL,
        "regime_bar_type": (
            f"{settings.instrument_id_str}-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
        ),
    }

    windows: list[tuple[datetime, datetime, datetime]] = []
    oos_start = _add_months(WF_START, IS_MONTHS)
    while _add_months(oos_start, OOS_MONTHS) <= WF_END:
        windows.append(
            (_add_months(oos_start, -IS_MONTHS), oos_start, _add_months(oos_start, OOS_MONTHS))
        )
        oos_start = _add_months(oos_start, OOS_MONTHS)
    print(f"windows: {len(windows)} (IS {IS_MONTHS}m / OOS {OOS_MONTHS}m)\n")

    oos_by_name: dict[str, dict] = {}
    final_cell: dict | None = None

    stage_a = {"baseline": cell_base}
    for buffer in (0.0, 0.25, 0.5):
        stage_a[f"rx={buffer}"] = {**cell_base, "regime_exit_buffer": buffer}
    is_a, oos_a = _run_stage("Stage A: regime-exit buffer", stage_a, windows, rates, spec, settings)
    oos_by_name.update(oos_a)
    best_name = _pick(is_a)
    best_cell = stage_a[best_name]
    best_is = is_a[best_name]["median_car"] or float("-inf")
    print(f"  -> selected: {best_name} (IS median car {best_is:+.2%})\n")

    stage_b = {f"chand={k}": {**best_cell, "chandelier_k": k} for k in (3.5, 4.0)}
    is_b, oos_b = _run_stage(
        "Stage B: chandelier trail (on stage-A winner)",
        stage_b,
        windows,
        rates,
        spec,
        settings,
    )
    oos_by_name.update(oos_b)
    b_name = _pick(is_b)
    if (is_b[b_name]["median_car"] or float("-inf")) > best_is:
        best_name, best_cell, best_is = b_name, stage_b[b_name], is_b[b_name]["median_car"]
        final_cell = best_cell
        print(f"  -> selected: {b_name} (IS median car {best_is:+.2%})\n")
    else:
        print(f"  -> kept stage-A winner {best_name}\n")

    stage_c = {f"time={n}": {**best_cell, "time_stop_bars": n} for n in (8, 16, 24)}
    is_c, oos_c = _run_stage(
        "Stage C: time stop (on current winner)",
        stage_c,
        windows,
        rates,
        spec,
        settings,
    )
    oos_by_name.update(oos_c)
    c_name = _pick(is_c)
    if (is_c[c_name]["median_car"] or float("-inf")) > best_is:
        best_name, best_cell = c_name, stage_c[c_name]
        final_cell = best_cell
        print(f"  -> selected: {c_name} (IS median car {is_c[c_name]['median_car']:+.2%})\n")
    else:
        print(f"  -> kept previous winner {best_name}\n")

    print("Final OOS summary")
    for name in dict.fromkeys(("baseline", best_name)):
        if name in oos_by_name:
            print(f"  {_fmt_agg(name, oos_by_name[name])}")
    print(f"\nfinal cell: {final_cell if final_cell is not None else best_cell}")


if __name__ == "__main__":
    main()
