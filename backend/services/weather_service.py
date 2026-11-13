from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from collections import OrderedDict
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# --- Tunables ---
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_SECONDS = 12.0
MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 0.5
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Prefer Indian places first (ambiguous names like "Aurangabad"), then fall back worldwide.
# Set to None to search worldwide only.
DEFAULT_COUNTRY_CODE: str | None = "IN"

MAX_CITY_CHARS = 100
WEATHER_CACHE_TTL_SECONDS = 600     # 10 minutes
GEO_CACHE_TTL_SECONDS = 86400       # 24 hours
CACHE_MAX_ENTRIES = 1000

# WMO weather interpretation codes used by Open-Meteo
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


class LocationNotFoundError(ValueError):
    """The place could not be found (bad input; map to HTTP 404/400)."""


class WeatherServiceError(RuntimeError):
    """Open-Meteo failed or returned bad data (map to HTTP 502/503)."""


# ---------------------------------------------------------------- small TTL cache
class _TTLCache:
    def __init__(self, ttl: float, maxsize: int) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return copy.deepcopy(value)  # callers can't mutate the cached copy

    def set(self, key: Any, value: Any) -> None:
        self._data[key] = (time.monotonic() + self._ttl, copy.deepcopy(value))
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)


_geo_cache = _TTLCache(GEO_CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES)
_weather_cache = _TTLCache(WEATHER_CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES)

# ---------------------------------------------------------------- shared HTTP client
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """One pooled IPv4 client for the whole app."""
    global _client
    if _client is None or _client.is_closed:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=6.0),
            transport=transport,
            headers={
                "User-Agent": "SmartKrishiFarmingAssistant/1.0 (https://smart-farming-assistant-opal.vercel.app)",
                "Accept": "application/json",
            },
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def aclose() -> None:
    """Call on app shutdown (e.g. FastAPI lifespan)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_json(url: str, params: dict[str, Any], what: str) -> dict[str, Any]:
    """GET with retries/backoff on timeouts, connection errors, 429 and 5xx."""
    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise WeatherServiceError(f"Weather service returned invalid {what} data.")
            return data
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                break
        except httpx.TransportError as exc:  # includes timeouts
            last_exc = exc
        except ValueError as exc:  # invalid JSON
            raise WeatherServiceError(f"Weather service returned invalid {what} data.") from exc

        if attempt < MAX_ATTEMPTS:
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning("%s request failed (attempt %d/%d): %s", what, attempt, MAX_ATTEMPTS, last_exc)
            await asyncio.sleep(delay)

    logger.error("%s API failed: %s", what, last_exc)
    if isinstance(last_exc, httpx.TimeoutException):
        raise WeatherServiceError(f"The {what} service timed out. Please try again.") from last_exc
    raise WeatherServiceError(f"Could not retrieve {what} data.") from last_exc


# ---------------------------------------------------------------- geocoding
def _clean_city(city: str | None) -> str:
    city = re.sub(r"[\x00-\x1f\x7f]", " ", city or "")
    city = re.sub(r"\s+", " ", city).strip()
    if not city:
        raise ValueError("Please provide a city or town.")
    if len(city) > MAX_CITY_CHARS:
        raise ValueError(f"Location name is too long (max {MAX_CITY_CHARS} characters).")
    return city


async def _geocode(city: str, language: str, country_code: str | None) -> dict[str, Any]:
    cache_key = (city.lower(), language, country_code)
    cached = _geo_cache.get(cache_key)
    if cached is not None:
        return cached

    # "Meerut, Uttar Pradesh" -> search "Meerut", use the rest as a disambiguation hint.
    name, _, hint = city.partition(",")
    name, hint = name.strip(), hint.strip().lower()
    if not name:
        raise ValueError("Please provide a city or town.")

    for code in ([country_code, None] if country_code else [None]):
        params: dict[str, Any] = {
            "name": name,
            "count": 10 if hint else 1,
            "language": language,
            "format": "json",
        }
        if code:
            params["countryCode"] = code

        data = await _get_json(GEO_URL, params, "location")
        results = data.get("results") or []
        if hint:
            matches = [
                r for r in results
                if hint in f"{r.get('admin1', '')} {r.get('country', '')}".lower()
            ]
            results = matches or results

        if results:
            location = results[0]
            if location.get("latitude") is None or location.get("longitude") is None:
                raise WeatherServiceError(f"Invalid coordinates returned for '{city}'.")
            _geo_cache.set(cache_key, location)
            return location

    raise LocationNotFoundError(f"No location found for '{city}'.")


# ---------------------------------------------------------------- weather
def _describe(code: Any) -> str | None:
    try:
        return WEATHER_CODES.get(int(code))
    except (TypeError, ValueError):
        return None


def _daily_rows(daily: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn Open-Meteo's column layout into one dict per day."""
    dates = daily.get("time") or []
    rows = []
    for i, date in enumerate(dates):
        row: dict[str, Any] = {"date": date}
        for key, values in daily.items():
            if key != "time" and isinstance(values, list) and i < len(values):
                row[key] = values[i]
        row["weather_description"] = _describe(row.get("weather_code"))
        rows.append(row)
    return rows


async def get_weather(
    city: str,
    *,
    language: str = "en",
    country_code: str | None = DEFAULT_COUNTRY_CODE,
) -> dict[str, Any]:
    """Fetch current weather and a 3-day forecast for a city or town.

    Accepts "Meerut" or "Meerut, Uttar Pradesh". Raises LocationNotFoundError /
    ValueError for bad input, WeatherServiceError if Open-Meteo fails.
    """
    city = _clean_city(city)
    location = await _geocode(city, language, country_code)
    lat, lon = location["latitude"], location["longitude"]

    weather_key = (round(lat, 2), round(lon, 2))
    forecast = _weather_cache.get(weather_key)

    if forecast is None:
        forecast_params = {
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,"
                "precipitation,weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
            ),
            "forecast_days": 3,
            "timezone": "auto",
        }
        forecast = await _get_json(FORECAST_URL, forecast_params, "forecast")
        if not forecast.get("current"):
            raise WeatherServiceError("Current weather data is unavailable.")
        _weather_cache.set(weather_key, forecast)

    current = dict(forecast["current"])
    current["weather_description"] = _describe(current.get("weather_code"))
    daily = forecast.get("daily") or {}

    return {
        "source": "Open-Meteo",
        "location": {
            "name": location.get("name", city),
            "region": location.get("admin1"),
            "country": location.get("country"),
            "latitude": lat,
            "longitude": lon,
        },
        "current": current,
        "daily": daily,                       # original column layout (unchanged)
        "daily_forecast": _daily_rows(daily), # same data, one dict per day
        "units": {
            "current": forecast.get("current_units", {}),
            "daily": forecast.get("daily_units", {}),
        },
        "timezone": forecast.get("timezone"),
    }