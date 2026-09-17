import json
from urllib.parse import parse_qs, urlparse

import pytest
from nautilus_trader.model import InstrumentId

import sngw_trader.data.open_interest as open_interest
from sngw_trader.data.open_interest import (
    OpenInterestPoint,
    fetch_open_interest,
    open_interest_data_type,
    parse_open_interest_rows,
    query_open_interest,
    register_open_interest,
    raw_open_interest_to_point,
    wrap_open_interest,
)
from nautilus_trader.persistence.catalog import ParquetDataCatalog


BASE_TS_MS = 1_757_520_000_000
VERIFIED_ROW_AT_200 = [
    str(BASE_TS_MS + 5 * 60 * 1000),
    "2845992.89000001579",
    "28459.9289000001579",
    "2154294886.41585239886718",
]
VERIFIED_ROW_AT_100 = [
    str(BASE_TS_MS),
    "2843398.92000001612",
    "28433.9892000001612",
    "2152452982.44001220284",
]
VERIFIED_ROW_WITH_INVALID_PERIOD = VERIFIED_ROW_AT_100 + ["5m"]


def test_open_interest_point_round_trips_dict_and_bytes():
    point = OpenInterestPoint(
        ts_event=300,
        ts_init=300,
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        open_interest=123.5,
    )
    assert OpenInterestPoint.from_dict(point.to_dict()) == point
    assert OpenInterestPoint.from_bytes(point.to_bytes()) == point


def test_open_interest_point_round_trips_arrow_batch():
    point = OpenInterestPoint(
        ts_event=300,
        ts_init=300,
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        open_interest=123.5,
    )
    batch = point.encode_record_batch_py([point])
    assert OpenInterestPoint.decode_record_batch_py({}, batch) == [point]


def test_catalog_round_trip_returns_custom_data(tmp_path):
    register_open_interest()
    catalog = ParquetDataCatalog(str(tmp_path))
    points = [
        OpenInterestPoint(100, 100, InstrumentId.from_str("BTC-USDT-SWAP.OKX"), 100.0),
        OpenInterestPoint(200, 200, InstrumentId.from_str("BTC-USDT-SWAP.OKX"), 101.0),
    ]
    catalog.write_data(
        [wrap_open_interest(point) for point in points],
        data_cls=OpenInterestPoint,
    )
    result = query_open_interest(catalog, "BTC-USDT-SWAP.OKX")
    assert [item.data.open_interest for item in result] == [100.0, 101.0]


def test_query_open_interest_filters_range_and_sorts_catalog_results():
    points = [
        OpenInterestPoint(
            ts_event=BASE_TS_MS * 1_000_000,
            ts_init=BASE_TS_MS * 1_000_000,
            instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            open_interest=100.0,
        ),
        OpenInterestPoint(
            ts_event=(BASE_TS_MS + 10 * 60 * 1000) * 1_000_000,
            ts_init=(BASE_TS_MS + 10 * 60 * 1000) * 1_000_000,
            instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            open_interest=102.0,
        ),
        OpenInterestPoint(
            ts_event=(BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
            ts_init=(BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
            instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            open_interest=101.0,
        ),
    ]

    class Catalog:
        def __init__(self):
            self.kwargs = None

        def query(self, **kwargs):
            self.kwargs = kwargs
            return [wrap_open_interest(point) for point in points]

    catalog = Catalog()
    result = query_open_interest(
        catalog,
        "BTC-USDT-SWAP.OKX",
        start=(BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
        end=(BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
    )

    assert [item.data.open_interest for item in result] == [101.0]
    assert catalog.kwargs == {
        "data_cls": OpenInterestPoint,
        "identifiers": ["BTC-USDT-SWAP.OKX"],
        "start": (BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
        "end": (BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
    }


def test_query_open_interest_returns_unsorted_catalog_results_in_time_order():
    points = [
        OpenInterestPoint(
            ts_event=(BASE_TS_MS + 10 * 60 * 1000) * 1_000_000,
            ts_init=(BASE_TS_MS + 10 * 60 * 1000) * 1_000_000,
            instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            open_interest=102.0,
        ),
        OpenInterestPoint(
            ts_event=BASE_TS_MS * 1_000_000,
            ts_init=BASE_TS_MS * 1_000_000,
            instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            open_interest=100.0,
        ),
    ]

    class Catalog:
        def query(self, **kwargs):
            return [wrap_open_interest(point) for point in points]

    result = query_open_interest(Catalog(), "BTC-USDT-SWAP.OKX")

    assert [item.data.open_interest for item in result] == [100.0, 102.0]


def test_register_open_interest_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(open_interest, "_registered", False)
    monkeypatch.setattr(
        open_interest,
        "register_custom_data_class",
        lambda data_cls: calls.append(data_cls),
    )

    register_open_interest()
    register_open_interest()

    assert calls == [OpenInterestPoint]


def test_parse_open_interest_rows_filters_deduplicates_and_sorts():
    rows = [
        VERIFIED_ROW_AT_200,
        VERIFIED_ROW_AT_100,
        VERIFIED_ROW_AT_200,
        ["300", "99", "1", "2"],
    ]

    points = parse_open_interest_rows(
        rows,
        "BTC-USDT-SWAP.OKX",
        start_ms=BASE_TS_MS,
        end_ms=BASE_TS_MS + 5 * 60 * 1000,
    )

    assert [point.ts_event for point in points] == [
        BASE_TS_MS * 1_000_000,
        (BASE_TS_MS + 5 * 60 * 1000) * 1_000_000,
    ]
    assert [point.open_interest for point in points] == [float(VERIFIED_ROW_AT_100[1]), float(VERIFIED_ROW_AT_200[1])]


def test_raw_open_interest_to_point_accepts_verified_dict_shape():
    point = raw_open_interest_to_point(
        {
            "ts": "200",
            "oi": "12.5",
            "oiCcy": "1.25",
            "oiUsd": "100000",
        },
        "BTC-USDT-SWAP.OKX",
    )

    assert point.ts_event == point.ts_init == 200_000_000
    assert point.open_interest == 12.5


def test_open_interest_data_type_records_source_provenance():
    metadata = open_interest_data_type("BTC-USDT-SWAP.OKX").metadata

    assert metadata["source_endpoint"] == (
        "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
    )
    assert metadata["open_interest_source"] == "OKX oi contract-value field"


def test_open_interest_data_type_accepts_source_override():
    metadata = open_interest_data_type("BTC-USDT-SWAP.OKX", source="custom").metadata
    assert metadata["open_interest_source"] == "custom"
    # 기본값 불변
    assert (
        open_interest_data_type("BTC-USDT-SWAP.OKX").metadata["open_interest_source"]
        == "OKX oi contract-value field"
    )


def test_parse_open_interest_rows_rejects_non_5m_period_shape():
    with pytest.raises(ValueError, match="5-minute"):
        parse_open_interest_rows(
            [VERIFIED_ROW_WITH_INVALID_PERIOD],
            "BTC-USDT-SWAP.OKX",
            0,
            1_000,
        )


def test_parse_open_interest_rows_rejects_malformed_values():
    with pytest.raises(ValueError, match="timestamp"):
        parse_open_interest_rows(
            [["not-a-timestamp", "1", "1", "1"]],
            "BTC-USDT-SWAP.OKX",
            0,
            1_000,
        )


def test_parse_open_interest_rows_rejects_coarser_gap():
    with pytest.raises(ValueError, match="5-minute"):
        parse_open_interest_rows(
            [
                [str(BASE_TS_MS), "1", "1", "1"],
                [str(BASE_TS_MS + 10 * 60 * 1000), "2", "2", "2"],
            ],
            "BTC-USDT-SWAP.OKX",
            BASE_TS_MS,
            BASE_TS_MS + 10 * 60 * 1000,
        )


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                [str(BASE_TS_MS + 5 * 60 * 1000), "2", "2", "2"],
                [str(BASE_TS_MS + 10 * 60 * 1000), "3", "3", "3"],
            ],
            "start",
        ),
        (
            [
                [str(BASE_TS_MS), "1", "1", "1"],
                [str(BASE_TS_MS + 5 * 60 * 1000), "2", "2", "2"],
            ],
            "end",
        ),
    ],
)
def test_parse_open_interest_rows_rejects_truncated_requested_range(rows, message):
    with pytest.raises(ValueError, match=f"{message}.*5-minute|5-minute.*{message}"):
        parse_open_interest_rows(
            rows,
            "BTC-USDT-SWAP.OKX",
            BASE_TS_MS,
            BASE_TS_MS + 10 * 60 * 1000,
        )


def test_fetch_open_interest_follows_older_end_cursor(monkeypatch):
    pages = [
        [
            [str(BASE_TS_MS + 20 * 60 * 1000), "5", "5", "5"],
            [str(BASE_TS_MS + 15 * 60 * 1000), "4", "4", "4"],
        ],
        [
            [str(BASE_TS_MS + 10 * 60 * 1000), "3", "3", "3"],
            [str(BASE_TS_MS + 5 * 60 * 1000), "2", "2", "2"],
            [str(BASE_TS_MS), "1", "1", "1"],
        ],
    ]
    requests = []

    class Response:
        def __init__(self, rows):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"code": "0", "data": self.rows}).encode()

    def fake_urlopen(request, timeout):
        requests.append((urlparse(request.full_url), timeout))
        return Response(pages[len(requests) - 1])

    monkeypatch.setattr("sngw_trader.data.open_interest.urllib.request.urlopen", fake_urlopen)

    points = fetch_open_interest(
        "BTC-USDT-SWAP",
        start_ms=BASE_TS_MS,
        end_ms=BASE_TS_MS + 20 * 60 * 1000,
    )

    assert [point.ts_event for point in points] == [
        (BASE_TS_MS + offset * 5 * 60 * 1000) * 1_000_000
        for offset in range(5)
    ]
    assert [parse_qs(url.query)["end"][0] for url, _ in requests] == [
        str(BASE_TS_MS + 25 * 60 * 1000),
        str(BASE_TS_MS + 15 * 60 * 1000),
    ]
    assert all(parse_qs(url.query)["limit"] == ["100"] for url, _ in requests)


def test_fetch_open_interest_rejects_non_5m_period_without_http(monkeypatch):
    def unexpected_request(*args, **kwargs):
        raise AssertionError("non-5m period reached HTTP")

    monkeypatch.setattr(
        "sngw_trader.data.open_interest.urllib.request.urlopen",
        unexpected_request,
    )
    with pytest.raises(ValueError, match="only 5m"):
        fetch_open_interest(
            "BTC-USDT-SWAP", start_ms=BASE_TS_MS, end_ms=BASE_TS_MS, period="15m"
        )


def test_fetch_open_interest_rejects_non_object_json(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(
        "sngw_trader.data.open_interest.urllib.request.urlopen",
        lambda request, timeout: Response(),
    )
    with pytest.raises(ValueError, match="JSON object"):
        fetch_open_interest(
            "BTC-USDT-SWAP", start_ms=BASE_TS_MS, end_ms=BASE_TS_MS
        )
