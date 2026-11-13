from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# The data.gov.in API key travels in the query string, and httpx logs full
# request URLs at INFO level. Keep that logger quiet so the key never lands
# in log files.
logging.getLogger("httpx").setLevel(logging.WARNING)

MANDI_API_URL = os.getenv(
    "MANDI_API_URL",
    "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070",
)

HEADERS = {
    "User-Agent": "SmartFarmingAssistant/1.0",
    "Accept": "application/json",
}

# --- Tunables ---------------------------------------------------------------
BATCH_SIZE = 100
MAX_RECORDS_TO_SCAN = 2000
REQUEST_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
OVERALL_TIMEOUT_SECONDS = 30.0   # total time budget across all pages/retries
RETRY_ATTEMPTS = 2               # extra tries for timeouts / 429 / 5xx
RETRY_BACKOFF_SECONDS = 1.0
STALE_AFTER_DAYS = 3

CACHE_TTL_SECONDS = 3600         # successful results with data
EMPTY_CACHE_TTL_SECONDS = 300    # "no records found" results expire sooner
CACHE_MAX_ENTRIES = 256

# India Standard Time (the API's arrival dates are Indian calendar dates).
IST = timezone(timedelta(hours=5, minutes=30))

# Common crop names -> names AGMARKNET actually uses.
COMMODITY_ALIASES: dict[str, list[str]] = {
    "rice": ["rice", "paddy(dhan)(common)"],
    "paddy": ["paddy(dhan)(common)", "rice"],
    "soybean": ["soyabean", "soyabeans", "soybean"],
    "soyabean": ["soyabean", "soyabeans", "soybean"],
    "maize": ["maize"],
    "corn": ["maize"],
}
# Exact spelling to send as a server-side filter (the API is case-sensitive).
COMMODITY_API_NAMES: dict[str, str] = {
    "rice": "Rice",
    "paddy": "Paddy(Dhan)(Common)",
    "soybean": "Soyabean",
    "soyabean": "Soyabean",
    "maize": "Maize",
    "corn": "Maize",
}

_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")

# key -> (timestamp, result); OrderedDict lets us evict the oldest entry.
_CACHE: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _field(record: dict[str, Any], name: str) -> str:
    """Safely read a record field as a stripped, casefolded string."""
    return str(record.get(name) or "").strip().casefold()


def _api_case(value: str) -> str:
    """Names that already have capitals ("NCT of Delhi", "Jammu and Kashmir")
    are sent as typed; plain lowercase input is title-cased."""
    return value if any(ch.isupper() for ch in value) else value.title()


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _accepted_names(commodity: str) -> set[str]:
    key = commodity.casefold()
    return {key, *COMMODITY_ALIASES.get(key, [])}


def _is_exact(name: str, accepted: set[str]) -> bool:
    return name in accepted


def _commodity_matches(name: str, accepted: set[str]) -> bool:
    """Exact name, or the accepted name as a whole word (e.g. 'Onion Green')."""
    if not name:
        return False
    if name in accepted:
        return True
    return any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", name)
        for alias in accepted
    )


def _cache_get(key: tuple) -> tuple[float, dict[str, Any]] | None:
    entry = _CACHE.get(key)
    if entry is not None:
        _CACHE.move_to_end(key)
    return entry


def _cache_set(key: tuple, result: dict[str, Any]) -> None:
    _CACHE[key] = (time.time(), result)
    _CACHE.move_to_end(key)
    while len(_CACHE) > CACHE_MAX_ENTRIES:
        _CACHE.popitem(last=False)


def _cache_is_fresh(cached_at: float, result: dict[str, Any]) -> bool:
    ttl = CACHE_TTL_SECONDS if result["count"] else EMPTY_CACHE_TTL_SECONDS
    return time.time() - cached_at < ttl


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------
async def _get_json(client: httpx.AsyncClient, params: dict[str, str]) -> dict:
    """GET one page with retries. Errors never include the URL (it has the key)."""
    last_error = "Could not connect to the official mandi API."

    for attempt in range(RETRY_ATTEMPTS + 1):
        is_last = attempt == RETRY_ATTEMPTS
        try:
            response = await client.get(MANDI_API_URL, params=params, headers=HEADERS)

            if response.status_code in (429, 500, 502, 503, 504) and not is_last:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue

            response.raise_for_status()
            payload = response.json()

        except httpx.TimeoutException:
            last_error = "The official mandi API timed out. Please try again later."
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                message = "The mandi API rejected the API key (check DATA_GOV_API_KEY)."
            elif status == 429:
                message = "The mandi API rate limit was reached. Please try again later."
            else:
                message = f"Mandi API returned HTTP error status: {status}"
            raise RuntimeError(message) from None  # `from None`: hide URL/key
        except httpx.RequestError as exc:
            logger.error("Mandi API network/TLS error: %r", exc)
            last_error = "Could not connect to the official mandi API."
        except ValueError:
            raise RuntimeError("The official mandi API returned invalid JSON.") from None
        else:
            if not isinstance(payload, dict):
                raise RuntimeError("The official mandi API returned an unexpected response.")
            return payload

        if not is_last:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise RuntimeError(last_error)


async def _scan(
    client: httpx.AsyncClient,
    api_key: str,
    server_filters: dict[str, str],
    accepted: set[str],
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
    deadline: float,
) -> tuple[list[dict[str, Any]], bool]:
    """Page through the API and keep records matching all requested filters."""
    matches: list[dict[str, Any]] = []
    offset = 0
    reported_total: int | None = None
    partial = False

    while offset < MAX_RECORDS_TO_SCAN:
        if time.monotonic() >= deadline:
            partial = True
            break

        params = {
            "api-key": api_key,
            "format": "json",
            "limit": str(BATCH_SIZE),
            "offset": str(offset),
        }
        for field, value in server_filters.items():
            params[f"filters[{field}]"] = value

        payload = await _get_json(client, params)

        records = payload.get("records", [])
        if not isinstance(records, list):
            raise RuntimeError("The official mandi API returned an invalid records list.")

        if payload.get("total") is not None:
            try:
                reported_total = int(payload["total"])
            except (TypeError, ValueError):
                reported_total = None

        # Client-side checks stay as a safety net (and handle case differences).
        for record in records:
            if not isinstance(record, dict):
                continue
            if clean_state and _field(record, "state") != clean_state.casefold():
                continue
            if clean_district and _field(record, "district") != clean_district.casefold():
                continue
            if not _commodity_matches(_field(record, "commodity"), accepted):
                continue
            matches.append(record)

        offset += len(records)

        if not records or len(records) < BATCH_SIZE:
            break
        if reported_total is not None and offset >= reported_total:
            break
        if len(matches) >= requested_limit:
            break

    return matches, partial



SNAPSHOT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "mandi_snapshot.json"
)


def _load_snapshot_prices(
    clean_commodity: str,
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
) -> dict[str, Any] | None:
    if not os.path.exists(SNAPSHOT_PATH):
        return None
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read mandi snapshot: %s", exc)
        return None

    accepted = _accepted_names(clean_commodity)
    matches = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if clean_state and _field(record, "state") != clean_state.casefold():
            continue
        if clean_district and _field(record, "district") != clean_district.casefold():
            continue
        if not _commodity_matches(_field(record, "commodity"), accepted):
            continue
        matches.append(record)

    exact = [r for r in matches if _is_exact(_field(r, "commodity"), accepted)]
    if exact:
        matches = exact

    matches.sort(
        key=lambda r: _parse_date(r.get("arrival_date")) or date.min,
        reverse=True,
    )
    matches = matches[:requested_limit]

    arrival_dates = [
        d for d in (_parse_date(r.get("arrival_date")) for r in matches) if d
    ]
    latest = max(arrival_dates) if arrival_dates else None

    return {
        "source": "Official Data.gov.in (AGMARKNET Snapshot)",
        "commodity": clean_commodity,
        "state": clean_state,
        "district": clean_district,
        "count": len(matches),
        "records": matches,
        "latest_arrival_date": latest.isoformat() if latest else None,
        "data_stale": False,
        "partial": False,
        "cached": True,
        "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
        "note": (
            f"Showing {len(matches)} verified AGMARKNET records for {clean_commodity}."
            if matches
            else f"No market records found for {clean_commodity}."
        ),
    }


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
async def get_mandi_prices(
    commodity: str,
    state: str | None = None,
    district: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Fetch current mandi prices from AGMARKNET (data.gov.in) with caching."""
    if not isinstance(commodity, str) or not commodity.strip():
        raise ValueError("Commodity cannot be empty.")

    api_key = os.getenv("DATA_GOV_API_KEY")
    if not api_key:
        raise RuntimeError("DATA_GOV_API_KEY is missing from backend/.env.")

    clean_commodity = commodity.strip()
    clean_state = state.strip() if isinstance(state, str) and state.strip() else None
    clean_district = (
        district.strip() if isinstance(district, str) and district.strip() else None
    )

    try:
        requested_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        requested_limit = 50

    # The key includes the limit, so a small request can't poison a larger one.
    cache_key = (
        clean_commodity.casefold(),
        (clean_state or "").casefold(),
        (clean_district or "").casefold(),
        requested_limit,
    )

    cached = _cache_get(cache_key)
    if cached and _cache_is_fresh(*cached):
        logger.info("Returning cached mandi prices.")
        result = copy.deepcopy(cached[1])
        result["cached"] = True
        return result

    try:
        result = await _fetch_prices(
            api_key, clean_commodity, clean_state, clean_district, requested_limit
        )
    except RuntimeError as exc:
        # Prefer old data over no data when the government API is down.
        if cached:
            logger.warning("Mandi API failed (%s); serving expired cache.", exc)
            result = copy.deepcopy(cached[1])
            result["cached"] = True
            result["note"] = (
                f"Live mandi data is unavailable ({exc}) Showing the last "
                "successfully retrieved records."
            )
            return result
        snapshot = _load_snapshot_prices(
            clean_commodity, clean_state, clean_district, requested_limit
        )
        if snapshot is not None:
            logger.warning("Live mandi API unreachable (%s); serving snapshot.", exc)
            _cache_set(cache_key, snapshot)
            return snapshot
        raise

    _cache_set(cache_key, result)
    out = copy.deepcopy(result)
    out["cached"] = False
    return out


async def _fetch_prices(
    api_key: str,
    clean_commodity: str,
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
) -> dict[str, Any]:
    accepted = _accepted_names(clean_commodity)
    deadline = time.monotonic() + OVERALL_TIMEOUT_SECONDS

    # Ask the API to filter by commodity/state/district so we don't download
    # thousands of unrelated rows. The API is case-sensitive, hence .title().
    primary_filters = {
        "commodity": COMMODITY_API_NAMES.get(
            clean_commodity.casefold(), clean_commodity.title()
        )
    }
    if clean_state:
        primary_filters["state"] = _api_case(clean_state)
    if clean_district:
        primary_filters["district"] = _api_case(clean_district)

    transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=8.0),
        transport=transport,
        verify=False,
    ) as client:
        matches, partial = await _scan(
            client, api_key, primary_filters, accepted,
            clean_state, clean_district, requested_limit, deadline,
        )

        # If exact-case server filters found nothing, fall back to a wider scan
        # (state filter only) with case-insensitive matching on our side.
        if not matches:
            fallback_filters = {"state": clean_state} if clean_state else {}
            matches, partial = await _scan(
                client, api_key, fallback_filters, accepted,
                clean_state, clean_district, requested_limit, deadline,
            )

    # Prefer exact commodity names ("Potato") over word matches ("Sweet Potato").
    exact = [r for r in matches if _is_exact(_field(r, "commodity"), accepted)]
    if exact:
        matches = exact

    # Newest first, so truncating to `limit` keeps the freshest records.
    matches.sort(
        key=lambda r: _parse_date(r.get("arrival_date")) or date.min,
        reverse=True,
    )
    matches = matches[:requested_limit]

    arrival_dates = [
        d for d in (_parse_date(r.get("arrival_date")) for r in matches) if d
    ]
    latest = max(arrival_dates) if arrival_dates else None

    today = datetime.now(IST).date()
    is_stale = latest is None or (today - latest).days > STALE_AFTER_DAYS

    if matches:
        note = "Official AGMARKNET records retrieved successfully."
        if partial:
            note += " The search hit its time limit, so results may be incomplete."
    else:
        note = (
            f"No official market records found for {clean_commodity}"
            + (f" in {clean_state}" if clean_state else "")
            + (f", {clean_district}" if clean_district else "")
            + "."
        )

    return {
        "source": "Official Data.gov.in (AGMARKNET)",
        "commodity": clean_commodity,
        "state": clean_state,
        "district": clean_district,
        "count": len(matches),
        "records": matches,
        "latest_arrival_date": latest.isoformat() if latest else None,
        "data_stale": is_stale,
        "partial": partial,
        "fetched_at": datetime.now(IST).isoformat(timespec="seconds"),
        "note": note,
    }