from sngw_trader.indicators.bar_aggregator import BarAggregator

M = 60 * 1_000_000_000


def _feed(agg, open_minute, o, h, l, c):
    """A 1m bar that OPENS at open_minute closes at open_minute+1."""
    return agg.update((open_minute + 1) * 60_000_000_000, o, h, l, c)


def test_30m_merges_and_flushes():
    agg = BarAggregator(bucket_seconds=1800)
    assert _feed(agg, 0, 100, 101, 99, 100.5) is None
    assert _feed(agg, 1, 101, 102, 99.5, 101) is None
    done = _feed(agg, 30, 101, 102, 101, 101.5)  # opens new 30m bucket
    assert done is not None
    assert done.open == 100
    assert done.high == 102
    assert done.low == 99
    assert done.close == 101
    assert done.ts_open_ns == 0
    assert done.ts_close_ns == 30 * 60_000_000_000


def test_daily_boundary_utc():
    agg = BarAggregator(bucket_seconds=86400)
    m0 = 23 * 60  # minute 1380 of day 0 -> opens 23:00, closes 23:01
    for m in (m0 := 23 * 60, m0 + 1, m0 + 59):
        assert _feed(agg, m, 100, 101, 99, 100.5) is None
    done = _feed(agg, m0 + 60, 100.5, 102, 100, 101)  # first bar of next day
    assert done is not None
    assert done.ts_open_ns == m0 * M
    assert done.ts_close_ns == (m0 + 60) * 60_000_000_000
    assert done.close == 100.5


def test_incomplete_bucket_never_emitted():
    agg = BarAggregator(bucket_seconds=86400)
    assert _feed(agg, 0, 1, 2, 0.5, 1.5) is None
    assert _feed(agg, 1, 1.5, 2, 1, 1.2) is None
    assert _feed(agg, 2, 1.5, 2, 1, 1.2) is None
