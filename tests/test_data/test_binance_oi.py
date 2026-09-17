from datetime import date, datetime, timezone

from sngw_trader.data.binance_oi import parse_metrics_csv, scan_binance_oi

HEADER = "create_time,symbol,sum_open_interest,sum_open_interest_value"


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def _five_minute_csv(day: str = "2023-06-01") -> str:
    return _csv(
        f"{day} 00:00:00,BTCUSDT,111645.70,3036897096",
        f"{day} 00:05:00,BTCUSDT,111650.87,3039706128",
        f"{day} 00:10:00,BTCUSDT,111706.19,3042117013",
    )


def _coarse_csv(day: str) -> str:
    return _csv(
        f"{day} 00:00:00,BTCUSDT,1.0,1.0",
        f"{day} 00:25:00,BTCUSDT,1.1,1.1",
        f"{day} 00:50:00,BTCUSDT,1.2,1.2",
    )


def test_parse_metrics_csv_returns_five_minute_points() -> None:
    points, interval = parse_metrics_csv(_five_minute_csv())
    assert interval == "5m"
    assert len(points) == 3
    expected = int(
        datetime(2023, 6, 1, 0, 5, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert points[1].ts_event == expected
    assert points[1].ts_init == expected
    assert points[1].open_interest == 111650.87
    assert str(points[1].instrument_id) == "BTC-USDT-SWAP.OKX"


def test_parse_metrics_csv_rejects_coarser_interval() -> None:
    text = _csv(
        "2026-09-15 00:00:00,BTCUSDT,103513.56,8088539305",
        "2026-09-15 00:25:00,BTCUSDT,103351.07,8059947582",
        "2026-09-15 00:50:00,BTCUSDT,103176.44,8039975408",
    )
    points, interval = parse_metrics_csv(text)
    assert points == []
    assert interval == "25m"


def test_parse_metrics_csv_unknown_on_short_input() -> None:
    assert parse_metrics_csv("create_time,symbol\n") == ([], "unknown")
    assert parse_metrics_csv("") == ([], "unknown")


def test_scan_skips_corrupt_days_and_resumes_5m() -> None:
    days = {
        date(2023, 6, 1): _five_minute_csv("2023-06-01"),
        date(2023, 6, 2): _coarse_csv("2023-06-02"),
        date(2023, 6, 3): _five_minute_csv("2023-06-03"),
        date(2023, 6, 4): _five_minute_csv("2023-06-04"),
    }
    points, era_end = scan_binance_oi(date(2023, 6, 1), date(2023, 6, 5), fetch=days.get)
    assert era_end == date(2023, 6, 5)
    assert len(points) == 9
    assert [p.ts_event for p in points] == sorted(p.ts_event for p in points)


def test_scan_skips_malformed_row_day_and_resumes_5m() -> None:
    # valid header, malformed row (non-numeric open_interest) -> parse raises
    bad = _csv(
        "2023-06-02 00:00:00,BTCUSDT,not-a-number,1.0",
        "2023-06-02 00:05:00,BTCUSDT,1.1,1.1",
    )
    days = {
        date(2023, 6, 1): _five_minute_csv("2023-06-01"),
        date(2023, 6, 2): bad,
        date(2023, 6, 3): _five_minute_csv("2023-06-03"),
    }
    points, era_end = scan_binance_oi(date(2023, 6, 1), date(2023, 6, 3), fetch=days.get)
    assert era_end == date(2023, 6, 4)
    assert len(points) == 6
    assert all(p.ts_event < int(datetime(2023, 6, 2, tzinfo=timezone.utc).timestamp() * 1e9)
               or p.ts_event >= int(datetime(2023, 6, 3, tzinfo=timezone.utc).timestamp() * 1e9)
               for p in points)


def test_scan_stops_at_era_end_and_dedupes() -> None:
    days = {
        date(2023, 5, 31): _five_minute_csv("2023-05-31"),
        date(2023, 6, 1): _five_minute_csv("2023-06-01"),
        date(2023, 6, 2): _csv(
            "2023-06-02 00:00:00,BTCUSDT,1.0,1.0",
            "2023-06-02 00:25:00,BTCUSDT,1.1,1.1",
            "2023-06-02 00:50:00,BTCUSDT,1.2,1.2",
        ),
    }
    points, era_end = scan_binance_oi(
        date(2023, 5, 30), date(2023, 6, 30), fetch=days.get
    )
    assert era_end == date(2023, 6, 2)
    # 30일은 404(None)로 스킵, 5m era 2일치 = 6 points (288 rows가 아닌 축소 샘플)
    assert len(points) == 6
    assert all(str(p.instrument_id) == "BTC-USDT-SWAP.OKX" for p in points)


def test_scan_returns_all_5m_when_era_never_ends() -> None:
    days = {date(2023, 6, d): _five_minute_csv(f"2023-06-0{d}") for d in (1, 2, 3)}
    points, era_end = scan_binance_oi(date(2023, 6, 1), date(2023, 6, 3), fetch=days.get)
    assert era_end is None
    assert len(points) == 9


def test_scan_dedupes_overlapping_rows_across_days() -> None:
    day1 = _csv(
        "2023-06-01 00:00:00,BTCUSDT,1.0,1.0",
        "2023-06-01 00:05:00,BTCUSDT,1.1,1.1",
    )
    day2 = _csv(
        "2023-06-01 00:05:00,BTCUSDT,9.9,9.9",
        "2023-06-01 00:10:00,BTCUSDT,1.2,1.2",
    )
    days = {date(2023, 6, 1): day1, date(2023, 6, 2): day2}
    points, era_end = scan_binance_oi(date(2023, 6, 1), date(2023, 6, 2), fetch=days.get)
    assert era_end is None
    assert len(points) == 3
    assert [p.ts_event for p in points] == sorted(p.ts_event for p in points)
