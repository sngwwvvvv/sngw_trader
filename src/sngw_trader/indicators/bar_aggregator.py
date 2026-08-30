"""Aggregate 1-minute source bars into 30m or daily (UTC) buckets.

Pure logic, no Nautilus imports, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

NS = 1_000_000_000


@dataclass(frozen=True)
class CompletedBar:
    ts_open_ns: int
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float


# ponytail: drops the final incomplete bucket; acceptable for daily/30m aggregation
class BarAggregator:
    def __init__(self, bucket_seconds: int, source_seconds: int = 60) -> None:
        self._bucket_ns = bucket_seconds * NS
        self._source_ns = source_seconds * NS
        self._bucket = -1
        self._o = self._h = self._l = self._c = 0.0
        self._ts_open = 0

    def update(self, ts_close_ns: int, o: float, h: float, l: float, c: float) -> CompletedBar | None:
        ts_open = ts_close_ns - self._source_ns
        bucket = ts_open // self._bucket_ns
        if self._bucket < 0:
            self._bucket = bucket
            self._o, self._h, self._l, self._c = o, h, l, c
            self._ts_open = ts_open
            return None
        if bucket == self._bucket:
            self._h = max(self._h, h)
            self._l = min(self._l, l)
            self._c = c
            return None
        done = CompletedBar(
            ts_open_ns=self._ts_open,
            ts_close_ns=(self._bucket + 1) * self._bucket_ns,
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
        )
        self._bucket = bucket
        self._o, self._h, self._l, self._c = o, h, l, c
        self._ts_open = ts_open
        return done