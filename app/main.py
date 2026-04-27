from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database import DBSession, create_db_and_tables
from app.models import StoreService, StoreStatus, StoreType, Stores
from app.redis import check_redis_connection, clear_search_cache
from app.search import ALLOWED_SERVICES, router as search_router

STORE_ID_PATTERN = r"^S\d{4,16}$"
PHONE_PATTERN = r"^\d{3}-\d{3}-\d{4}$"
STATE_PATTERN = r"^[A-Za-z]{2}$"
COUNTRY_PATTERN = r"^[A-Za-z]{3}$"


def _validate_hours_value(field_name: str, value: str) -> None:
    if value == "closed":
        return

    try:
        open_text, close_text = value.split("-")
        open_hours, open_minutes = open_text.split(":")
        close_hours, close_minutes = close_text.split(":")
        open_total = int(open_hours) * 60 + int(open_minutes)
        close_total = int(close_hours) * 60 + int(close_minutes)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be in HH:MM-HH:MM format or 'closed'") from exc

    if open_total >= close_total:
        raise ValueError(f"{field_name} open time must be earlier than close time")


class StoreHoursUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mon: str | None = None
    tue: str | None = None
    wed: str | None = None
    thu: str | None = None
    fri: str | None = None
    sat: str | None = None
    sun: str | None = None

    @model_validator(mode="after")
    def validate_hours(self) -> "StoreHoursUpdate":
        provided = self.model_dump(exclude_none=True)
        if not provided:
            raise ValueError("hours must include at least one day")

        for field_name, value in provided.items():
            _validate_hours_value(field_name, value)

        return self


class StorePartialUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=1, max_length=12, pattern=PHONE_PATTERN)
    services: list[str] | None = None
    status: StoreStatus | None = None
    hours: StoreHoursUpdate | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "StorePartialUpdateRequest":
        if (
            self.name is None
            and self.phone is None
            and self.services is None
            and self.status is None
            and self.hours is None
        ):
            raise ValueError("provide at least one field to update")

        if self.name is not None:
            self.name = self.name.strip()
            if not self.name:
                raise ValueError("name cannot be empty")

        if self.phone is not None:
            self.phone = self.phone.strip()
            if not self.phone:
                raise ValueError("phone cannot be empty")

        if self.services is not None:
            normalized_services = [service.strip().lower() for service in self.services if service.strip()]
            invalid_services = [service for service in normalized_services if service not in ALLOWED_SERVICES]
            if invalid_services:
                raise ValueError(
                    f"unsupported service(s): {', '.join(sorted(set(invalid_services)))}"
                )
            if len(normalized_services) != len(set(normalized_services)):
                raise ValueError("duplicate services are not allowed")
            self.services = normalized_services

        return self


class StoreHoursCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mon: str
    tue: str
    wed: str
    thu: str
    fri: str
    sat: str
    sun: str

    @model_validator(mode="after")
    def validate_hours(self) -> "StoreHoursCreate":
        for field_name, value in self.model_dump().items():
            _validate_hours_value(field_name, value)

        return self


class StoreCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_id: str = Field(min_length=1, max_length=16, pattern=STORE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    store_type: StoreType
    status: StoreStatus = StoreStatus.ACTIVE
    address_street: str = Field(min_length=1, max_length=255)
    address_city: str = Field(min_length=1, max_length=120)
    address_state: str = Field(min_length=2, max_length=2, pattern=STATE_PATTERN)
    address_postal_code: str = Field(min_length=5, max_length=5, pattern=r"^\d{5}$")
    address_country: str = Field(default="USA", min_length=3, max_length=3, pattern=COUNTRY_PATTERN)
    phone: str = Field(min_length=1, max_length=12, pattern=PHONE_PATTERN)
    services: list[str] = Field(default_factory=list)
    hours: StoreHoursCreate
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def validate_payload(self) -> "StoreCreateRequest":
        self.store_id = self.store_id.strip()
        self.name = self.name.strip()
        self.address_street = self.address_street.strip()
        self.address_city = self.address_city.strip()
        self.address_state = self.address_state.strip().upper()
        self.address_postal_code = self.address_postal_code.strip()
        self.address_country = self.address_country.strip().upper()
        self.phone = self.phone.strip()

        if not self.store_id:
            raise ValueError("store_id cannot be empty")
        if not self.name:
            raise ValueError("name cannot be empty")

        normalized_services = [service.strip().lower() for service in self.services if service.strip()]
        invalid_services = [service for service in normalized_services if service not in ALLOWED_SERVICES]
        if invalid_services:
            raise ValueError(f"unsupported service(s): {', '.join(sorted(set(invalid_services)))}")
        if len(normalized_services) != len(set(normalized_services)):
            raise ValueError("duplicate services are not allowed")
        self.services = normalized_services

        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")

        return self


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to create database tables and verify Redis at startup."""
    create_db_and_tables()
    
    # Check Redis connection
    if not check_redis_connection():
        print("WARNING: Redis connection failed. Caching and rate limiting will not work.")
    
    yield
    print("Shutting down...")


app = FastAPI(title="Store Locator API", lifespan=lifespan)
app.include_router(search_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def health_db(db: DBSession) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database connection failed: {exc}") from exc


@app.get("/health/redis")
def health_redis() -> dict[str, str]:
    """Check Redis connection status."""
    if check_redis_connection():
        return {"status": "ok", "redis": "connected"}
    raise HTTPException(status_code=500, detail="Redis connection failed")


def _hours_to_dict(store: Stores) -> dict[str, str]:
    return {
        "mon": store.hours_mon,
        "tue": store.hours_tue,
        "wed": store.hours_wed,
        "thu": store.hours_thu,
        "fri": store.hours_fri,
        "sat": store.hours_sat,
        "sun": store.hours_sun,
    }


def _store_response(store: Stores) -> dict[str, object]:
    return {
        "store_id": store.store_id,
        "name": store.name,
        "store_type": store.store_type.value,
        "status": store.status.value,
        "latitude": float(store.latitude),
        "longitude": float(store.longitude),
        "address_street": store.address_street,
        "address_city": store.address_city,
        "address_state": store.address_state,
        "address_postal_code": store.address_postal_code,
        "address_country": store.address_country,
        "phone": store.phone,
        "services": [service.service_name for service in store.services],
        "hours": _hours_to_dict(store),
    }


def _geocode_store_location(payload: StoreCreateRequest) -> tuple[float, float]:
    if payload.latitude is not None and payload.longitude is not None:
        return float(payload.latitude), float(payload.longitude)

    from app.search import GeocodeResult, StoreSearchRequest, geocode_location

    geocode_request = StoreSearchRequest(
        address=(
            f"{payload.address_street}, {payload.address_city}, {payload.address_state} "
            f"{payload.address_postal_code}, {payload.address_country}"
        )
    )
    geocoded = geocode_location(geocode_request)
    if not isinstance(geocoded, GeocodeResult):
        raise HTTPException(status_code=502, detail="Failed to geocode store address")
    return geocoded.latitude, geocoded.longitude



@app.get("/api/admin/stores")
def get_all_stores(db: DBSession) -> list[dict[str, str]]:
    """Admin endpoint to retrieve all stores (for testing purposes)."""
    result = db.execute(text("SELECT store_id, name FROM stores"))
    return [{"store_id": row["store_id"], "name": row["name"]} for row in result]

@app.get("/api/admin/stores/{store_id}")
def get_store_by_id(db: DBSession, store_id: str = Path(..., pattern=STORE_ID_PATTERN)) -> dict[str, str]:
    """Admin endpoint to retrieve a store by ID (for testing purposes)."""
    result = db.execute(text("SELECT store_id, name FROM stores WHERE store_id = :store_id"), {"store_id": store_id})
    row = result.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return {"store_id": row["store_id"], "name": row["name"]}

@app.patch("/api/admin/stores/{store_id}")
def update_store(
    db: DBSession,
    store_id: str = Path(..., pattern=STORE_ID_PATTERN),
    payload: StorePartialUpdateRequest = ...,
) -> dict[str, object]:
    """Admin endpoint to partially update a store."""
    store = db.execute(
        select(Stores).options(selectinload(Stores.services)).where(Stores.store_id == store_id)
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")

    if payload.name is not None:
        store.name = payload.name
    if payload.phone is not None:
        store.phone = payload.phone
    if payload.status is not None:
        store.status = payload.status
    if payload.hours is not None:
        if payload.hours.mon is not None:
            store.hours_mon = payload.hours.mon
        if payload.hours.tue is not None:
            store.hours_tue = payload.hours.tue
        if payload.hours.wed is not None:
            store.hours_wed = payload.hours.wed
        if payload.hours.thu is not None:
            store.hours_thu = payload.hours.thu
        if payload.hours.fri is not None:
            store.hours_fri = payload.hours.fri
        if payload.hours.sat is not None:
            store.hours_sat = payload.hours.sat
        if payload.hours.sun is not None:
            store.hours_sun = payload.hours.sun
    if payload.services is not None:
        store.services.clear()
        db.flush()
        store.services.extend(
            StoreService(service_name=service_name) for service_name in payload.services
        )

    db.commit()
    clear_search_cache()

    updated_store = db.execute(
        select(Stores).options(selectinload(Stores.services)).where(Stores.store_id == store_id)
    ).scalar_one_or_none()
    if updated_store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return _store_response(updated_store)

@app.delete("/api/admin/stores/{store_id}")
def delete_store(db: DBSession, store_id: str = Path(..., pattern=STORE_ID_PATTERN)) -> dict[str, str]:
    """Admin endpoint to deactivate a store by ID (soft delete)."""
    store = db.execute(select(Stores).where(Stores.store_id == store_id)).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")

    store.status = StoreStatus.INACTIVE
    db.commit()
    clear_search_cache()
    return {"status": StoreStatus.INACTIVE.value, "store_id": store_id}

@app.post("/api/admin/stores", status_code=201)
def create_store(payload: StoreCreateRequest, db: DBSession) -> dict[str, object]:
    """Admin endpoint to create a new store with auto-geocoding when coordinates are missing."""
    try:
        latitude, longitude = _geocode_store_location(payload)

        store = Stores(
            store_id=payload.store_id,
            name=payload.name,
            store_type=payload.store_type,
            status=payload.status,
            latitude=latitude,
            longitude=longitude,
            address_street=payload.address_street,
            address_city=payload.address_city,
            address_state=payload.address_state,
            address_postal_code=payload.address_postal_code,
            address_country=payload.address_country,
            phone=payload.phone,
            hours_mon=payload.hours.mon,
            hours_tue=payload.hours.tue,
            hours_wed=payload.hours.wed,
            hours_thu=payload.hours.thu,
            hours_fri=payload.hours.fri,
            hours_sat=payload.hours.sat,
            hours_sun=payload.hours.sun,
            services=[StoreService(service_name=service_name) for service_name in payload.services],
        )
        db.add(store)
        db.commit()
        clear_search_cache()

        created_store = db.execute(
            select(Stores).options(selectinload(Stores.services)).where(Stores.store_id == payload.store_id)
        ).scalar_one()
        return _store_response(created_store)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"Store already exists or violates a database constraint: {exc.orig}") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create store: {exc}") from exc
    
    