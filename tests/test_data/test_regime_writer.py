from datetime import date, datetime, timezone
from pathlib import Path

from sngw_trader.data.regime_writer import input_hash, master_sessions, write_manifest


def test_master_sessions_are_intersection() -> None:
    etf = {
        "XLY": {date(2020, 1, 2), date(2020, 1, 3)},
        "XLI": {date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)},
    }
    # pad other 7 with 1-2
    for name in ("XLB", "XLF", "XLK", "XLE", "XLP", "XLU", "XLV"):
        etf[name] = {date(2020, 1, 2), date(2020, 1, 3)}
    assert master_sessions(etf) == [date(2020, 1, 2), date(2020, 1, 3)]


def test_manifest_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "regime_manifest.json"
    write_manifest(path, {"source_snapshot_id": "abc", "n_snapshots": 1})
    text = path.read_text(encoding="utf-8")
    assert "abc" in text


def test_input_hash_stable() -> None:
    a = input_hash({"oas": "1,2,3"})
    b = input_hash({"oas": "1,2,3"})
    c = input_hash({"oas": "1,2,4"})
    assert a == b
    assert a != c


from datetime import date, timedelta
from pathlib import Path

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.data.regime_snapshot import RegimeSnapshot
from sngw_trader.data.regime_universe import SECTOR_ETFS
from sngw_trader.data.regime_writer import run_write


def _weekdays(n: int) -> list[date]:
    out: list[date] = []
    d = date(2019, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_run_write_emits_post_warmup_snapshots(tmp_path: Path) -> None:
    days = _weekdays(280)
    inputs = {
        "etf_dates": {s: set(days) for s in SECTOR_ETFS},
        "bar_ts": {d: i + 1 for i, d in enumerate(days)},
        "oas": {d: 4.0 for d in days},
        "vix": {d: 15.0 for d in days},
        "vxv": {d: 16.0 for d in days},
        "copper": {d: 3.0 for d in days},
        "gold": {d: 1500.0 for d in days},
        "feature_version": "macro-proxy-2axis-v1",
        "downloaded_at": "2026-09-15T00:00:00+00:00",
    }
    catalog = ParquetDataCatalog(str(tmp_path))
    sid = run_write(catalog, inputs, tmp_path / "regime_manifest.json")
    snaps = catalog.custom_data(cls=RegimeSnapshot)
    assert sid
    assert len(snaps) >= 28
    assert (tmp_path / "regime_manifest.json").exists()