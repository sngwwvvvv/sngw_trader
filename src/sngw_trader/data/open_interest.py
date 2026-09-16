import json
import math
import time
import urllib.parse
import urllib.request
from datetime import timezone
from typing import TYPE_CHECKING

from nautilus_trader.core.nautilus_pyo3.model import register_custom_data_class
from nautilus_trader.core.data import Data
from nautilus_trader.model import CustomData, DataType, InstrumentId
from nautilus_trader.model.custom import customdataclass_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog

if TYPE_CHECKING:
    from sngw_trader.config.settings import Settings


_OPEN_INTEREST_HISTORY_URL = (
    "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
)
_PAGE_SIZE = 100
_FIVE_MINUTE_MS = 5 * 60 * 1000
OI_INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"
_OI_SYMBOL = "BTC-USDT-SWAP"


@customdataclass_pyo3()
class OpenInterestPoint(Data):
    instrument_id: InstrumentId
    open_interest: float


def _raw_timestamp_and_interest(row: list[str] | dict[str, str]) -> tuple[str, str]:
    if isinstance(row, list):
        if len(row) != 4:
            raise ValueError("expected the verified 5-minute OKX row shape of four fields")
        return row[0], row[1]
    if isinstance(row, dict):
        try:
            return row["ts"], row["oi"]
        except KeyError as exc:
            raise ValueError("OKX 5-minute open-interest row requires ts and oi") from exc
    raise ValueError("OKX 5-minute open-interest row must be a list or dict")


def _require_oi_instrument(instrument_id: str) -> None:
    if instrument_id != OI_INSTRUMENT_ID:
        raise ValueError(
            f"OI data supports only {OI_INSTRUMENT_ID}, got {instrument_id!r}"
        )


def raw_open_interest_to_point(
    row: list[str] | dict[str, str], instrument_id: str
) -> OpenInterestPoint:
    _require_oi_instrument(instrument_id)
    raw_ts, raw_oi = _raw_timestamp_and_interest(row)
    try:
        ts_ms = int(raw_ts)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid open-interest timestamp: {raw_ts!r}") from exc
    if ts_ms < 0:
        raise ValueError(f"invalid open-interest timestamp: {raw_ts!r}")
    try:
        open_interest = float(raw_oi)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid open-interest value: {raw_oi!r}") from exc
    if not math.isfinite(open_interest):
        raise ValueError(f"invalid open-interest value: {raw_oi!r}")
    return OpenInterestPoint(
        ts_event=ts_ms * 1_000_000,
        ts_init=ts_ms * 1_000_000,
        instrument_id=InstrumentId.from_str(instrument_id),
        open_interest=open_interest,
    )


def parse_open_interest_rows(
    rows: list[list[str] | dict[str, str]],
    instrument_id: str,
    start_ms: int,
    end_ms: int,
) -> list[OpenInterestPoint]:
    _require_oi_instrument(instrument_id)
    if end_ms < start_ms:
        raise ValueError("open-interest end must be greater than or equal to start")
    points_by_timestamp: dict[int, OpenInterestPoint] = {}
    for row in rows:
        point = raw_open_interest_to_point(row, instrument_id)
        ts_ms = point.ts_event // 1_000_000
        if start_ms <= ts_ms <= end_ms:
            points_by_timestamp[ts_ms] = point
    timestamps = sorted(points_by_timestamp)
    for timestamp in timestamps:
        if timestamp % _FIVE_MINUTE_MS:
            raise ValueError(
                f"open-interest timestamp {timestamp} is not aligned to the 5-minute interval"
            )
    for previous, current in zip(timestamps, timestamps[1:]):
        if current - previous != _FIVE_MINUTE_MS:
            raise ValueError(
                "open-interest rows are not contiguous 5-minute data; "
                f"gap from {previous} to {current}"
            )
    first_boundary = ((start_ms + _FIVE_MINUTE_MS - 1) // _FIVE_MINUTE_MS) * _FIVE_MINUTE_MS
    last_boundary = (end_ms // _FIVE_MINUTE_MS) * _FIVE_MINUTE_MS
    if first_boundary <= last_boundary:
        if not timestamps or timestamps[0] != first_boundary:
            raise ValueError(
                "open-interest rows do not cover the requested start 5-minute boundary"
            )
        if timestamps[-1] != last_boundary:
            raise ValueError(
                "open-interest rows do not cover the requested end 5-minute boundary"
            )
    return [points_by_timestamp[ts] for ts in timestamps]


def fetch_open_interest(
    inst_id: str,
    start_ms: int,
    end_ms: int,
    period: str = "5m",
) -> list[OpenInterestPoint]:
    if inst_id != _OI_SYMBOL:
        raise ValueError(
            f"OI data supports only {_OI_SYMBOL}.OKX, got {inst_id!r}"
        )
    if period != "5m":
        raise ValueError("OKX open-interest history supports only 5m period")
    if end_ms < start_ms:
        raise ValueError("open-interest end must be greater than or equal to start")

    rows: list[list[str] | dict[str, str]] = []
    end_cursor = end_ms + _FIVE_MINUTE_MS
    while True:
        params = {
            "instId": inst_id,
            "period": period,
            "end": str(end_cursor),
            "limit": str(_PAGE_SIZE),
        }
        url = _OPEN_INTEREST_HISTORY_URL + "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("OKX open-interest response must be a JSON object")
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX open-interest history error: {payload}")
        page = payload.get("data")
        if not isinstance(page, list):
            raise ValueError("OKX open-interest response data must be a list")
        if not page:
            break
        rows.extend(page)
        oldest = min(
            raw_open_interest_to_point(row, f"{inst_id}.OKX").ts_event // 1_000_000
            for row in page
        )
        if oldest >= end_cursor:
            raise RuntimeError("OKX open-interest pagination cursor did not move older")
        if oldest <= start_ms:
            break
        end_cursor = oldest
        time.sleep(0.1)
    return parse_open_interest_rows(rows, f"{inst_id}.OKX", start_ms, end_ms)


def download_open_interest(settings: "Settings", instrument_id: str) -> list[OpenInterestPoint]:
    _require_oi_instrument(instrument_id)
    if settings.catalog_start is None or settings.catalog_end is None:
        raise SystemExit("CATALOG_START and CATALOG_END are required for open-interest download")
    start_ms = int(settings.catalog_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(settings.catalog_end.astimezone(timezone.utc).timestamp() * 1000)
    return fetch_open_interest(
        instrument_id.split(".", 1)[0],
        start_ms,
        end_ms,
        period=settings.oi_period,
    )


def open_interest_data_type(instrument_id: str, source: str | None = None) -> DataType:
    _require_oi_instrument(instrument_id)
    return DataType(
        OpenInterestPoint,
        metadata={
            "instrument_id": instrument_id,
            "source_endpoint": _OPEN_INTEREST_HISTORY_URL,
            "open_interest_source": source or "OKX oi contract-value field",
        },
    )


def wrap_open_interest(point: OpenInterestPoint, source: str | None = None) -> CustomData:
    _require_oi_instrument(point.instrument_id.value)
    return CustomData(open_interest_data_type(point.instrument_id.value, source), point)


_registered = False


def register_open_interest() -> None:
    global _registered
    if not _registered:
        register_custom_data_class(OpenInterestPoint)
        _registered = True


def query_open_interest(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start: int | None = None,
    end: int | None = None,
) -> list[CustomData]:
    _require_oi_instrument(instrument_id)
    result = catalog.query(
        data_cls=OpenInterestPoint,
        identifiers=[instrument_id],
        start=start,
        end=end,
    )
    return [
        item
        for item in sorted(result, key=lambda item: item.data.ts_init)
        if (start is None or item.data.ts_init >= start)
        and (end is None or item.data.ts_init <= end)
    ]
