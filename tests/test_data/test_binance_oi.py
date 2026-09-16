from datetime import datetime, timezone

from sngw_trader.data.binance_oi import parse_metrics_csv

HEADER = "create_time,symbol,sum_open_interest,sum_open_interest_value"


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def _five_minute_csv() -> str:
    return _csv(
        "2023-06-01 00:00:00,BTCUSDT,111645.70,3036897096",
        "2023-06-01 00:05:00,BTCUSDT,111650.87,3039706128",
        "2023-06-01 00:10:00,BTCUSDT,111706.19,3042117013",
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
