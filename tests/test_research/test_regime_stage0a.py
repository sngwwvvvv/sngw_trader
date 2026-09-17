from datetime import date, timedelta

from sngw_trader.data.regime_universe import SECTOR_ETFS
from sngw_trader.research.regime_stage0a import evaluate_stage0a


def _snaps(n: int, sid="sid", ver="macro-proxy-2axis-v1"):
    class S:
        def __init__(self, i: int):
            self.session_date = date(2010, 1, 1).fromordinal(date(2010, 1, 1).toordinal() + i).isoformat()
            self.quality_code = 2
            self.regime_code = 1
            self.feature_version = ver
            self.source_snapshot_id = sid
    return [S(i) for i in range(n)]


def test_missing_etf_fails() -> None:
    etf = {s: {date(2020, 1, 2)} for s in SECTOR_ETFS if s != "XLK"}
    report = evaluate_stage0a(etf, _snaps(300), {"source_snapshot_id": "sid", "feature_version": "macro-proxy-2axis-v1"}, None)
    assert report.ok is False
    assert any("XLK" in r for r in report.reasons)


def test_version_mismatch_fails() -> None:
    etf = {s: {date(2020, 1, 2) + timedelta(days=i) for i in range(400)} for s in SECTOR_ETFS}
    report = evaluate_stage0a(
        etf, _snaps(300, ver="other"),
        {"source_snapshot_id": "sid", "feature_version": "macro-proxy-2axis-v1"},
        None,
    )
    assert report.ok is False
