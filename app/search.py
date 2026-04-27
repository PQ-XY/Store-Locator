from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from geopy.distance import geodesic
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import DBSession
from app.models import StoreService, StoreStatus, StoreType, Stores
from app.redis import (
    cache_get_geocode,
    cache_get_search,
    cache_set_geocode,
    cache_set_search,
    rate_limit_hour_check,
    rate_limit_hour_get_reset_time,
    rate_limit_minute_check,
    rate_limit_minute_get_reset_time,
)

router = APIRouter(prefix="/api/stores", tags=["stores"])

ALLOWED_SERVICES = {
    "pharmacy",
    "pickup",
    "returns",
    "optical",
    "photo_printing",
    "gift_wrapping",
    "automotive",
    "garden_center",
}

GEOCODE_CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
SEARCH_CACHE_TTL_SECONDS = 10 * 60
RATE_LIMIT_MINUTE_LIMIT = 10
RATE_LIMIT_HOUR_LIMIT = 100
RATE_LIMIT_MINUTE_WINDOW_SECONDS = 60
RATE_LIMIT_HOUR_WINDOW_SECONDS = 60 * 60


@dataclass(frozen=True)
class GeocodeResult:
    latitude: float
    longitude: float
    label: str


class StoreSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str | None = Field(default=None, description="Full street address")
    postal_code: str | None = Field(default=None, description="5-digit postal code")
    latitude: float | None = Field(default=None, description="Latitude coordinate")
    longitude: float | None = Field(default=None, description="Longitude coordinate")

    @model_validator(mode="after")
    def validate_location(self) -> "StoreSearchRequest":
        address = self.address.strip() if self.address else None
        postal_code = self.postal_code.strip() if self.postal_code else None

        if address:
            self.address = address
        if postal_code:
            self.postal_code = postal_code

        location_modes = [
            bool(self.address),
            bool(self.postal_code),
            self.latitude is not None or self.longitude is not None,
        ]
        if sum(location_modes) != 1:
            raise ValueError(
                "provide exactly one search mode: address, postal_code, or latitude+longitude"
            )

        if self.postal_code is not None:
            if not self.postal_code.isdigit() or len(self.postal_code) != 5:
                raise ValueError("postal_code must be a 5-digit ZIP code")

        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")

        return self

    @property
    def search_label(self) -> str:
        if self.address:
            return self.address
        if self.postal_code:
            return self.postal_code
        return f"{self.latitude}, {self.longitude}"


class SearchCoordinates(BaseModel):
    latitude: float
    longitude: float


class AppliedFilters(BaseModel):
    radius_miles: float
    services: list[str]
    store_types: list[StoreType]
    open_now: bool | None


class SearchMetadata(BaseModel):
    location_searched: str
    search_coordinates: SearchCoordinates
    applied_filters: AppliedFilters


class StoreSearchResult(BaseModel):
    store_id: str
    name: str
    address_street: str
    address_city: str
    address_state: str
    address_postal_code: str
    address_country: str
    store_type: StoreType
    services: list[str]
    phone: str
    hours: dict[str, str]
    status: StoreStatus
    distance_miles: float
    is_open_now: bool


class StoreSearchResponse(BaseModel):
    results: list[StoreSearchResult]
    search_metadata: SearchMetadata


def _normalize_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _rate_limit_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_search_rate_limit(request: Request) -> None:
    """Enforce rate limits (10/min and 100/hour per IP) using Redis."""
    ip_address = _rate_limit_key(request)

    # Check minute limit
    minute_ok = rate_limit_minute_check(ip_address, RATE_LIMIT_MINUTE_LIMIT, RATE_LIMIT_MINUTE_WINDOW_SECONDS)
    if not minute_ok:
        retry_after = rate_limit_minute_get_reset_time(ip_address, RATE_LIMIT_MINUTE_WINDOW_SECONDS)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: 10 requests per minute per IP address",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(RATE_LIMIT_MINUTE_LIMIT),
                "X-RateLimit-Remaining": "0",
            },
        )

    # Check hour limit
    hour_ok = rate_limit_hour_check(ip_address, RATE_LIMIT_HOUR_LIMIT, RATE_LIMIT_HOUR_WINDOW_SECONDS)
    if not hour_ok:
        retry_after = rate_limit_hour_get_reset_time(ip_address, RATE_LIMIT_HOUR_WINDOW_SECONDS)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: 100 requests per hour per IP address",
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(RATE_LIMIT_HOUR_LIMIT),
                "X-RateLimit-Remaining": "0",
            },
        )


def _build_geocode_query(payload: StoreSearchRequest) -> tuple[str, str]:
    if payload.address:
        query = payload.address
        if "usa" not in query.lower() and "united states" not in query.lower():
            query = f"{query}, USA"
        return f"address:{_normalize_text(payload.address)}", query

    if payload.postal_code:
        query = f"{payload.postal_code}, USA"
        return f"postal_code:{payload.postal_code}", query

    raise ValueError("geocode query can only be built for address or postal_code searches")


def geocode_location(payload: StoreSearchRequest) -> GeocodeResult:
    if payload.latitude is not None and payload.longitude is not None:
        return GeocodeResult(
            latitude=payload.latitude,
            longitude=payload.longitude,
            label=payload.search_label,
        )

    cache_key, query = _build_geocode_query(payload)
    
    # Check Redis cache
    cached_data = cache_get_geocode(cache_key)
    if cached_data is not None:
        return GeocodeResult(**cached_data)

    params = {
        "format": "jsonv2",
        "limit": 1,
        "q": query,
        "countrycodes": "us",
    }
    url = f"https://nominatim.openstreetmap.org/search?{urlencode(params)}"
    request = UrlRequest(
        url,
        headers={"User-Agent": "store-locator-app/1.0 (local development)"},
    )

    try:
        with urlopen(request, timeout=10) as response:
            payload_text = response.read().decode("utf-8")
    except (HTTPError, URLError) as exc:
        raise HTTPException(status_code=502, detail=f"Geocoding service unavailable: {exc}") from exc

    try:
        results = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Geocoding service returned invalid data") from exc

    if not results:
        raise HTTPException(status_code=404, detail="No location found for the supplied search input")

    first_result = results[0]
    geocode_result = GeocodeResult(
        latitude=float(first_result["lat"]),
        longitude=float(first_result["lon"]),
        label=first_result.get("display_name", payload.search_label),
    )
    
    # Store in Redis cache
    cache_set_geocode(cache_key, geocode_result.__dict__, GEOCODE_CACHE_TTL_SECONDS)
    return geocode_result


def _bounding_box(latitude: float, longitude: float, radius_miles: float) -> tuple[float, float, float, float]:
    latitude_delta = radius_miles / 69.0
    latitude_radians = math.radians(latitude)
    cosine = math.cos(latitude_radians)
    if abs(cosine) < 1e-6:
        longitude_delta = 180.0
    else:
        longitude_delta = radius_miles / (69.0 * abs(cosine))

    min_lat = max(-90.0, latitude - latitude_delta)
    max_lat = min(90.0, latitude + latitude_delta)
    min_lon = max(-180.0, longitude - longitude_delta)
    max_lon = min(180.0, longitude + longitude_delta)
    return min_lat, max_lat, min_lon, max_lon


def _hours_for_store(store: Stores) -> dict[str, str]:
    return {
        "mon": store.hours_mon,
        "tue": store.hours_tue,
        "wed": store.hours_wed,
        "thu": store.hours_thu,
        "fri": store.hours_fri,
        "sat": store.hours_sat,
        "sun": store.hours_sun,
    }


def _parse_hours(value: str) -> tuple[int, int] | None:
    if value == "closed":
        return None
    open_text, close_text = value.split("-")
    open_hours, open_minutes = open_text.split(":")
    close_hours, close_minutes = close_text.split(":")
    open_total = int(open_hours) * 60 + int(open_minutes)
    close_total = int(close_hours) * 60 + int(close_minutes)
    return open_total, close_total


def _current_minutes_and_weekday() -> tuple[int, int]:
    current = datetime.now().astimezone()
    return current.weekday(), current.hour * 60 + current.minute


def is_store_open_now(store: Stores) -> bool:
    weekday_index, current_minutes = _current_minutes_and_weekday()
    hours_map = [
        store.hours_mon,
        store.hours_tue,
        store.hours_wed,
        store.hours_thu,
        store.hours_fri,
        store.hours_sat,
        store.hours_sun,
    ]
    today_hours = hours_map[weekday_index]
    parsed = _parse_hours(today_hours)
    if parsed is None:
        return False
    open_minutes, close_minutes = parsed
    return open_minutes <= current_minutes < close_minutes


def _store_services(store: Stores) -> list[str]:
    return [service.service_name for service in store.services]


def _store_matches_services(store: Stores, requested_services: list[str]) -> bool:
    if not requested_services:
        return True
    store_services = set(_store_services(store))
    return set(requested_services).issubset(store_services)


def _validate_requested_services(requested_services: list[str]) -> list[str]:
    normalized = [service.strip().lower() for service in requested_services if service.strip()]
    invalid = [service for service in normalized if service not in ALLOWED_SERVICES]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported service filter(s): {', '.join(sorted(set(invalid)))}",
        )
    if len(normalized) != len(set(normalized)):
        raise HTTPException(status_code=400, detail="Duplicate services are not allowed")
    return normalized


def _build_search_cache_key(
    payload: StoreSearchRequest,
    radius_miles: float,
    services: list[str],
    store_types: list[StoreType],
    open_now: bool | None,
) -> str:
    cache_payload = {
        "payload": payload.model_dump(mode="json"),
        "radius_miles": radius_miles,
        "services": services,
        "store_types": [store_type.value for store_type in store_types],
        "open_now": open_now,
    }
    return json.dumps(cache_payload, sort_keys=True)


@router.post(
    "/search",
    response_model=StoreSearchResponse,
    dependencies=[Depends(enforce_search_rate_limit)],
)
def search_stores(
    payload: StoreSearchRequest,
    db: DBSession,
    radius_miles: float = Query(default=10, ge=1, le=100),
    services: list[str] = Query(default=[]),
    store_types: list[StoreType] = Query(default=[]),
    open_now: bool | None = Query(default=None),
) -> StoreSearchResponse:
    normalized_services = _validate_requested_services(services)
    location = geocode_location(payload)

    search_cache_key = _build_search_cache_key(
        payload=payload,
        radius_miles=radius_miles,
        services=normalized_services,
        store_types=store_types,
        open_now=open_now,
    )
    
    # Check Redis search cache
    cached_response = cache_get_search(search_cache_key)
    if cached_response is not None:
        return StoreSearchResponse.model_validate(cached_response)

    min_lat, max_lat, min_lon, max_lon = _bounding_box(location.latitude, location.longitude, radius_miles)

    query = (
        select(Stores)
        .options(selectinload(Stores.services))
        .where(Stores.status == StoreStatus.ACTIVE)
        .where(Stores.latitude.between(min_lat, max_lat))
        .where(Stores.longitude.between(min_lon, max_lon))
    )
    if store_types:
        query = query.where(Stores.store_type.in_(store_types))

    stores = list(db.scalars(query))
    search_results: list[StoreSearchResult] = []

    for store in stores:
        if not _store_matches_services(store, normalized_services):
            continue

        store_open_now = is_store_open_now(store)
        if open_now:
            if not store_open_now:
                continue

        distance_miles = geodesic(
            (location.latitude, location.longitude),
            (float(store.latitude), float(store.longitude)),
        ).miles

        if distance_miles > radius_miles:
            continue

        search_results.append(
            StoreSearchResult(
                store_id=store.store_id,
                name=store.name,
                address_street=store.address_street,
                address_city=store.address_city,
                address_state=store.address_state,
                address_postal_code=store.address_postal_code,
                address_country=store.address_country,
                store_type=store.store_type,
                services=_store_services(store),
                phone=store.phone,
                hours=_hours_for_store(store),
                status=store.status,
                distance_miles=round(distance_miles, 4),
                is_open_now=store_open_now,
            )
        )

    search_results.sort(key=lambda item: item.distance_miles)

    response = StoreSearchResponse(
        results=search_results,
        search_metadata=SearchMetadata(
            location_searched=location.label,
            search_coordinates=SearchCoordinates(latitude=location.latitude, longitude=location.longitude),
            applied_filters=AppliedFilters(
                radius_miles=radius_miles,
                services=normalized_services,
                store_types=store_types,
                open_now=open_now,
            ),
        ),
    )

    # Store in Redis cache
    cache_set_search(search_cache_key, response.model_dump(mode="json"), SEARCH_CACHE_TTL_SECONDS)
    return response
