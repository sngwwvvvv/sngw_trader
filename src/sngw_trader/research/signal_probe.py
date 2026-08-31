"""Daily ERMOM signal probe: does the sign predict future returns?

Diagnostics only. No orders, no nodes, no alpha decisions here.
Usage: uv run python -m sngw_trader.research.signal_probe
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from sngw_trader.config import load_settings
from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum
from sngw_trader.runners.backtest_okx import default_bar_type

DAY_NS = 86_400_000_000_000
W_F, W_E, L = 10, 10, 200


def load_daily_closes() -> list[int]:
    """[(day_index, close)] from the 1m catalog, UTC day buckets."""
    from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

    settings = load_settings()
    catalog = ParquetDataCatalog(str(settings.catalog_path))
    bars = catalog.bars(bar_types=[default_bar_type(settings.instrument_id_str)])
    closes: dict[int, float] = {}
    for bar in bars:
        day = int(bar.ts_init) // DAY_NS
        closes[day] = bar.close.as_double()  # 1m stream: last write wins = daily close
    return sorted(closes.items())


def main() -> None:
    days = load_daily_closes()
    print(f"daily bars: {len(days)}")

    ermom = ErrorAdjustedMomentum(W_F, W_E, L)
    series: list[tuple[int, float, float]] = []  # (day, close, ermom)
    for day, close in days:
        val = ermom.update(close)
        if val is not None:
            series.append((day, close, val))
    print(f"post-warmup points: {len(series)} "
          f"({len(series) / 365:.1f} years)")

    horizons = [1, 5, 10, 20]
    print(f"\n{'horizon':>7} {'regime':>7} {'n':>6} {'mean_fwd':>9} {'t_stat':>7} {'hit%':>6} {'uncond':>9}")
    for n in horizons:
        for regime, label in ((1, "+1"), (-1, "-1"), (0, "0")):
            fwd = []
            for i in range(len(series) - n):
                if regime_target_of(series[i][2]) != regime:
                    continue
                fwd.append(series[i + n][1] / series[i][1] - 1)
            uncond = [series[i + n][1] / series[i][1] - 1
                      for i in range(len(series) - n)]
            if len(fwd) < 5:
                print(f"{n:>7} {label:>7} {len(fwd):>6} {'-':>9} {'-':>7} {'-':>6} {mean(uncond)*100:>8.2f}%")
                continue
            m = mean(fwd)
            t = t_stat(fwd)
            hit = 100 * sum(1 for r in fwd if (r > 0) == (regime > 0) if regime != 0) / len(fwd) if regime != 0 else float("nan")
            print(f"{n:>7} {label:>7} {len(fwd):>6} {m*100:>8.2f}% {t:>7.2f} {hit:>5.1f}% {mean(uncond)*100:>8.2f}%")

    # gross daily-following strategy: position_t = sign(ermom_t), pnl = position * r_{t+1}
    rets = [series[i + 1][1] / series[i][1] - 1 for i in range(len(series) - 1)]
    pos = [1.0 if s[2] > 0 else -1.0 for s in series[:-1]]
    strat = [p * r for p, r in zip(pos, rets)]
    flips = sum(1 for i in range(1, len(pos)) if pos[i] != pos[i - 1])
    sharpe = mean(strat) / math.sqrt(variance(strat)) * math.sqrt(365) if variance(strat) > 0 else 0.0
    bh = mean(rets) / math.sqrt(variance(rets)) * math.sqrt(365) if variance(rets) > 0 else 0.0
    print(f"\ngross daily-following: sharpe(ann)={sharpe:.2f}  flips={flips}  "
          f"flip_cost@10bps={flips * 0.001:.2%}")
    print(f"buy&hold daily:        sharpe(ann)={bh:.2f}")

    # yearly breakdown, 5d horizon
    print("\nyearly, 5d fwd mean by regime sign:")
    by_year: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for i in range(len(series) - 5):
        year = datetime.fromtimestamp(series[i][0] * 86_400, tz=timezone.utc).year
        by_year[year].append((series[i][2], series[i + 5][1] / series[i][1] - 1))
    for year in sorted(by_year):
        longs = [r for v, r in by_year[year] if v > 0]
        shorts = [r for v, r in by_year[year] if v < 0]
        lm = f"{mean(longs)*100:>+6.2f}% (n={len(longs):>3})" if longs else "  -"
        sm = f"{mean(shorts)*100:>+6.2f}% (n={len(shorts):>3})" if shorts else "  -"
        print(f"  {year}: +1 {lm}   -1 {sm}")


def regime_target_of(v: float) -> int:
    return 1 if v > 0 else (-1 if v < 0 else 0)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def variance(xs: list[float]) -> float:
    m = mean(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def t_stat(xs: list[float]) -> float:
    if len(xs) < 3 or variance(xs) == 0:
        return float("nan")
    return mean(xs) / math.sqrt(variance(xs) / len(xs))


if __name__ == "__main__":
    main()
