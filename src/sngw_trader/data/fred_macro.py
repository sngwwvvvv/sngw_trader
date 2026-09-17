"""FRED daily observations (OAS, VIXCLS, VXVCLS) for the regime writer.

Only writer/data modules import this. Never place orders here.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime

FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"


def fred_api_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise SystemExit("FRED_API_KEY is required")
    return key


def parse_fred_observations(payload: dict) -> list[tuple[date, float]]:
    rows: list[tuple[date, float]] = []
    for obs in payload.get("observations", []):
        raw = str(obs.get("value", "")).strip()
        if not raw or raw == ".":
            continue
        rows.append((date.fromisoformat(obs["date"]), float(raw)))
    return rows


def fetch_fred_series(
    series_id: str,
    start: date | None,
    end: date | None,
    opener=None,
) -> list[tuple[date, float]]:
    if opener is None:
        opener = urllib.request.urlopen
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    params: dict[str, str] = {
        "series_id": series_id,
        "api_key": fred_api_key(),
        "file_type": "json",
    }
    if start is not None:
        params["observation_start"] = start.isoformat()
    if end is not None:
        params["observation_end"] = end.isoformat()
    req = urllib.request.Request(f"{FRED_OBS_URL}?{urllib.parse.urlencode(params)}")
    with opener(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    return parse_fred_observations(payload)
