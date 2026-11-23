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
OVERALL_TIMEOUT_SECONDS = 30.0
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 1.0
STALE_AFTER_DAYS = 3

CACHE_TTL_SECONDS = 3600
EMPTY_CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 512

IST = timezone(timedelta(hours=5, minutes=30))

# Canonical farmer input (English + common Hindi) -> exact AGMARKNET commodity names
# Ordered by most common APMC arrival name first!
COMMODITY_API_CANDIDATES: dict[str, list[str]] = {
    "rice": ["Paddy(Dhan)(Common)", "Rice", "Paddy(Dhan)(Basmati)"],
    "chawal": ["Paddy(Dhan)(Common)", "Rice", "Paddy(Dhan)(Basmati)"],
    "paddy": ["Paddy(Dhan)(Common)", "Paddy(Dhan)(Basmati)", "Rice"],
    "dhan": ["Paddy(Dhan)(Common)", "Paddy(Dhan)(Basmati)"],
    "wheat": ["Wheat"],
    "gehu": ["Wheat"],
    "gehun": ["Wheat"],
    "maize": ["Maize"],
    "corn": ["Maize"],
    "makka": ["Maize"],
    "bajra": ["Bajra(Pearl Millet/Cumbu)"],
    "pearl millet": ["Bajra(Pearl Millet/Cumbu)"],
    "jowar": ["Jowar(Sorghum)"],
    "sorghum": ["Jowar(Sorghum)"],
    "ragi": ["Ragi (Finger Millet)"],
    "barley": ["Barley (Jau)"],
    "soybean": ["Soyabean"],
    "soyabean": ["Soyabean"],
    "mustard": ["Mustard"],
    "sarson": ["Mustard"],
    "groundnut": ["Groundnut"],
    "moongphali": ["Groundnut"],
    "sunflower": ["Sunflower"],
    "cotton": ["Cotton"],
    "kapas": ["Cotton"],
    "sugarcane": ["Sugarcane"],
    "ganna": ["Sugarcane"],
    "chana": ["Bengal Gram(Gram)(Whole)", "Kabuli Chana(Chickpea-White)"],
    "gram": ["Bengal Gram(Gram)(Whole)"],
    "chickpea": ["Bengal Gram(Gram)(Whole)", "Kabuli Chana(Chickpea-White)"],
    "tur": ["Arhar (Tur/Red Gram)(Whole)"],
    "arhar": ["Arhar (Tur/Red Gram)(Whole)"],
    "pigeon pea": ["Arhar (Tur/Red Gram)(Whole)"],
    "moong": ["Green Gram (Moong)(Whole)"],
    "green gram": ["Green Gram (Moong)(Whole)"],
    "urad": ["Black Gram (Urd Beans)(Whole)"],
    "black gram": ["Black Gram (Urd Beans)(Whole)"],
    "masoor": ["Lentil (Masur)(Whole)"],
    "lentil": ["Lentil (Masur)(Whole)"],
    "potato": ["Potato"],
    "aloo": ["Potato"],
    "onion": ["Onion"],
    "pyaz": ["Onion"],
    "tomato": ["Tomato"],
    "tamatar": ["Tomato"],
    "brinjal": ["Brinjal"],
    "baingan": ["Brinjal"],
    "eggplant": ["Brinjal"],
    "okra": ["Bhindi(Ladies Finger)"],
    "bhindi": ["Bhindi(Ladies Finger)"],
    "ladies finger": ["Bhindi(Ladies Finger)"],
    "cabbage": ["Cabbage"],
    "cauliflower": ["Cauliflower"],
    "gobhi": ["Cauliflower", "Cabbage"],
    "carrot": ["Carrot"],
    "gajar": ["Carrot"],
    "chilli": ["Green Chilli", "Dry Chillies"],
    "mirchi": ["Green Chilli", "Dry Chillies"],
    "garlic": ["Garlic"],
    "lahsun": ["Garlic"],
    "ginger": ["Ginger(Green)", "Ginger(Dry)"],
    "adrak": ["Ginger(Green)", "Ginger(Dry)"],
    "turmeric": ["Turmeric"],
    "haldi": ["Turmeric"],
    "coriander": ["Coriander(Leaves)", "Corriander seed"],
    "dhaniya": ["Coriander(Leaves)", "Corriander seed"],
    "banana": ["Banana", "Banana - Green"],
    "kela": ["Banana"],
    "mango": ["Mango", "Mango (Raw-Ripe)"],
    "aam": ["Mango"],
    "apple": ["Apple"],
    "seb": ["Apple"],
    "pomegranate": ["Pomegranate"],
    "anar": ["Pomegranate"],
    "grapes": ["Grapes"],
    "angoor": ["Grapes"],
    "orange": ["Orange"],
    "santra": ["Orange"],
    "lemon": ["Lemon", "Lime"],
    "nimbu": ["Lemon", "Lime"],
    "coconut": ["Coconut", "Tender Coconut"],
    "nariyal": ["Coconut", "Tender Coconut"],
}

# Build lowercase matching sets from COMMODITY_API_CANDIDATES
COMMODITY_ALIASES: dict[str, list[str]] = {
    k: [c.casefold() for c in v] for k, v in COMMODITY_API_CANDIDATES.items()
}

_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
_CACHE: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()

SNAPSHOT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "mandi_snapshot.json"
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _field(record: dict[str, Any], name: str) -> str:
    return str(record.get(name) or "").strip().casefold()


def _api_case(value: str) -> str:
    """Preserve mixed-case names ('NCT of Delhi', 'Jammu and Kashmir'); title-case plain inputs."""
    cleaned = value.strip()
    return cleaned if any(ch.isupper() for ch in cleaned) else cleaned.title()


def _parse_date(raw: Any) -> date | None:
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _api_candidates(commodity: str) -> list[str]:
    """Return ordered exact AGMARKNET commodity names to query server-side."""
    key = commodity.strip().casefold()
    if key in COMMODITY_API_CANDIDATES:
        return COMMODITY_API_CANDIDATES[key]
    return [_api_case(commodity)]


def _accepted_names(commodity: str) -> set[str]:
    key = commodity.strip().casefold()
    return {key, *COMMODITY_ALIASES.get(key, [])}


def _is_exact(name: str, accepted: set[str]) -> bool:
    return name in accepted


def _commodity_matches(name: str, accepted: set[str]) -> bool:
    if not name:
        return False
    if name in accepted:
        return True
    return any(
        re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", name)
        for alias in accepted
    )


def _dedupe_and_sort(records: list[dict[str, Any]], accepted: set[str], limit: int) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for r in records:
        sig = (
            _field(r, "state"),
            _field(r, "district"),
            _field(r, "market"),
            _field(r, "commodity"),
            str(r.get("arrival_date") or ""),
        )
        if sig not in seen:
            seen.add(sig)
            unique.append(r)

    exact = [r for r in unique if _is_exact(_field(r, "commodity"), accepted)]
    if exact:
        unique = exact

    unique.sort(
        key=lambda r: _parse_date(r.get("arrival_date")) or date.min,
        reverse=True,
    )
    return unique[:limit]


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
            raise RuntimeError(message) from None
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


# --------------------------------------------------------------------------
# Snapshot fallback (works offline or when a state/district has 0 live rows)
# --------------------------------------------------------------------------
def _query_snapshot_records(
    clean_commodity: str,
    clean_state: str | None,
    clean_district: str | None,
) -> list[dict[str, Any]]:
    if not os.path.exists(SNAPSHOT_PATH):
        return []
    try:
        with open(SNAPSHOT_PATH, "r", encoding="utf-8") as f:
            records = json.load(f)
    except Exception as exc:
        logger.warning("Failed to read mandi snapshot: %s", exc)
        return []

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
    return matches


def _load_snapshot_prices(
    clean_commodity: str,
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
) -> dict[str, Any] | None:
    if not os.path.exists(SNAPSHOT_PATH):
        return None

    accepted = _accepted_names(clean_commodity)
    scope_note = ""

    # Progressive fallback inside snapshot: District -> State -> National
    matches = _query_snapshot_records(clean_commodity, clean_state, clean_district)
    if not matches and clean_district:
        matches = _query_snapshot_records(clean_commodity, clean_state, None)
        if matches:
            scope_note = f"No snapshot rows in {clean_district}; showing {clean_state} mandis. "
    if not matches and clean_state:
        matches = _query_snapshot_records(clean_commodity, None, None)
        if matches:
            scope_note = f"No snapshot rows in {clean_state}; showing national mandi rates. "

    matches = _dedupe_and_sort(matches, accepted, requested_limit)
    arrival_dates = [d for d in (_parse_date(r.get("arrival_date")) for r in matches) if d]
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
            f"{scope_note}Showing {len(matches)} verified AGMARKNET records for {clean_commodity}."
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
    """Fetch mandi prices from AGMARKNET with alias resolution & progressive fallback."""
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
            return snapshot
        raise

    _cache_set(cache_key, result)
    out = copy.deepcopy(result)
    out["cached"] = False
    return out


async def _query_candidates_for_scope(
    client: httpx.AsyncClient,
    api_key: str,
    candidates: list[str],
    accepted: set[str],
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
    deadline: float,
) -> tuple[list[dict[str, Any]], bool]:
    """Query every candidate AGMARKNET commodity name for a given state/district scope."""
    combined: list[dict[str, Any]] = []
    any_partial = False

    for candidate in candidates:
        if time.monotonic() >= deadline or len(combined) >= requested_limit:
            break
        filters: dict[str, str] = {"commodity": candidate}
        if clean_state:
            filters["state"] = _api_case(clean_state)
        if clean_district:
            filters["district"] = _api_case(clean_district)

        rows, partial = await _scan(
            client, api_key, filters, accepted,
            clean_state, clean_district, requested_limit, deadline,
        )
        combined.extend(rows)
        any_partial = any_partial or partial

    return combined, any_partial


async def _fetch_prices(
    api_key: str,
    clean_commodity: str,
    clean_state: str | None,
    clean_district: str | None,
    requested_limit: int,
) -> dict[str, Any]:
    candidates = _api_candidates(clean_commodity)
    accepted = _accepted_names(clean_commodity)
    deadline = time.monotonic() + OVERALL_TIMEOUT_SECONDS
    fallback_note = ""

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=8.0),
        verify=False,
    ) as client:
        # Tier 1: Exact requested scope (Commodity aliases + State + District)
        matches, partial = await _query_candidates_for_scope(
            client, api_key, candidates, accepted,
            clean_state, clean_district, requested_limit, deadline,
        )

        # Tier 2: State-wide scan (if exact candidate filter missed a variant or district had 0 arrivals)
        if not matches and clean_state:
            state_filters = {"state": _api_case(clean_state)}
            state_rows, partial = await _scan(
                client, api_key, state_filters, accepted,
                clean_state, clean_district, requested_limit, deadline,
            )
            if state_rows:
                matches = state_rows
            elif clean_district:
                # Expand from District -> Whole State so farmer sees nearest mandis
                matches, partial = await _query_candidates_for_scope(
                    client, api_key, candidates, accepted,
                    clean_state, None, requested_limit, deadline,
                )
                if not matches:
                    matches, partial = await _scan(
                        client, api_key, state_filters, accepted,
                        clean_state, None, requested_limit, deadline,
                    )
                if matches:
                    fallback_note = (
                        f"No live arrivals reported today in {clean_district}; "
                        f"showing nearest mandis across {clean_state}. "
                    )

        # Tier 3: Local snapshot fallback for requested state/district before going national
        if not matches and (clean_state or clean_district):
            snap_rows = _query_snapshot_records(clean_commodity, clean_state, clean_district)
            if not snap_rows and clean_district:
                snap_rows = _query_snapshot_records(clean_commodity, clean_state, None)
            if snap_rows:
                matches = snap_rows
                fallback_note = (
                    f"No live arrivals reported today in {clean_state}; "
                    f"showing latest recorded {clean_commodity} mandi prices. "
                )

        # Tier 4: National / All-India mandi fallback (never return 0 records if crop exists)
        if not matches and clean_state:
            matches, partial = await _query_candidates_for_scope(
                client, api_key, candidates, accepted,
                None, None, min(requested_limit, 25), deadline,
            )
            if matches:
                fallback_note = (
                    f"{clean_commodity} is not actively traded in {clean_state} mandis today; "
                    f"showing current rates across major Indian mandis. "
                )

    matches = _dedupe_and_sort(matches, accepted, requested_limit)

    arrival_dates = [
        d for d in (_parse_date(r.get("arrival_date")) for r in matches) if d
    ]
    latest = max(arrival_dates) if arrival_dates else None

    today = datetime.now(IST).date()
    is_stale = latest is None or (today - latest).days > STALE_AFTER_DAYS

    if matches:
        note = fallback_note + "Official AGMARKNET records retrieved successfully."
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