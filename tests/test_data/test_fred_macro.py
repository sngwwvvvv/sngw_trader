from datetime import date, datetime, timezone

import pytest

from sngw_trader.data.fred_macro import fetch_fred_series, parse_fred_observations


def test_parse_skips_dot_missing() -> None:
    payload = {
        "observations": [
            {"date": "2020-01-02", "value": "4.5"},
            {"date": "2020-01-03", "value": "."},
            {"date": "2020-01-06", "value": "4.7"},
        ]
    }
    rows = parse_fred_observations(payload)
    assert rows == [(date(2020, 1, 2), 4.5), (date(2020, 1, 6), 4.7)]


def test_fetch_uses_injected_opener(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    calls = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"observations":[{"date":"2020-01-02","value":"1.25"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    def opener(req, timeout=30):
        calls["url"] = req.full_url if hasattr(req, "full_url") else str(req)
        return _Resp()

    rows = fetch_fred_series("BAMLH0A0HYM2", date(2020, 1, 1), date(2020, 1, 31), opener=opener)
    assert rows == [(date(2020, 1, 2), 1.25)]
    assert "api.stlouisfed.org" in calls["url"]
    assert "api_key=test-key" in calls["url"]
    assert "series_id=BAMLH0A0HYM2" in calls["url"]


def test_missing_api_key_exits(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        fetch_fred_series("VIXCLS", None, None, opener=lambda *a, **k: None)


def test_fetch_accepts_datetime_bounds(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    calls = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"observations":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    def opener(req, timeout=30):
        calls["url"] = req.full_url
        return _Resp()

    fetch_fred_series(
        "BAMLH0A0HYM2",
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 31, tzinfo=timezone.utc),
        opener=opener,
    )
    assert "observation_start=2020-01-01" in calls["url"]
    assert "observation_end=2020-01-31" in calls["url"]
    assert "T00%3A00%3A00" not in calls["url"]
