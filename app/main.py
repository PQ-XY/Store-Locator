from contextlib import asynccontextmanager
from datetime import datetime, timezone
import io

import bcrypt
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Path, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import and_, func, text
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.auth import require_permissions, router as auth_router
from app.database import DBSession, create_db_and_tables
from app.models import Role, StoreService, StoreStatus, StoreType, Stores, User, UserStatus
from app.redis import check_redis_connection, clear_search_cache
from app.search import ALLOWED_SERVICES, router as search_router

STORE_ID_PATTERN = r"^S\d{4,16}$"
USER_ID_PATTERN = r"^U\d{3,16}$"
PHONE_PATTERN = r"^\d{3}-\d{3}-\d{4}$"
STATE_PATTERN = r"^[A-Za-z]{2}$"
COUNTRY_PATTERN = r"^[A-Za-z]{3}$"
EMAIL_PATTERN = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"

CSV_IMPORT_HEADERS = [
    "store_id",
    "name",
    "store_type",
    "status",
    "latitude",
    "longitude",
    "address_street",
    "address_city",
    "address_state",
    "address_postal_code",
    "address_country",
    "phone",
    "services",
    "hours_mon",
    "hours_tue",
    "hours_wed",
    "hours_thu",
    "hours_fri",
    "hours_sat",
    "hours_sun",
]

CSV_REQUIRED_FIELDS = [
    "store_id",
    "name",
    "store_type",
    "status",
    "address_street",
    "address_city",
    "address_state",
    "address_postal_code",
    "address_country",
    "phone",
    "services",
    "hours_mon",
    "hours_tue",
    "hours_wed",
    "hours_thu",
    "hours_fri",
    "hours_sat",
    "hours_sun",
]


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


class UserCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=4, max_length=32, pattern=USER_ID_PATTERN)
    email: str = Field(min_length=3, max_length=255, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=8, max_length=255)
    role: str = Field(min_length=3, max_length=64)
    status: UserStatus = UserStatus.ACTIVE
    must_change_password: bool = True

    @model_validator(mode="after")
    def normalize(self) -> "UserCreateRequest":
        self.user_id = self.user_id.strip().upper()
        self.email = self.email.strip().lower()
        self.password = self.password.strip()
        self.role = self.role.strip().lower()
        return self


class UserUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str | None = Field(default=None, min_length=3, max_length=64)
    status: UserStatus | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "UserUpdateRequest":
        if self.role is None and self.status is None:
            raise ValueError("provide at least one field to update")
        if self.role is not None:
            self.role = self.role.strip().lower()
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
app.include_router(auth_router)


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


def _user_response(user: User) -> dict[str, object]:
    return {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role.name,
        "status": user.status.value,
        "must_change_password": user.must_change_password,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _get_role_or_404(db: DBSession, role_name: str) -> Role:
    role = db.execute(select(Role).where(Role.name == role_name)).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=400, detail=f"Role '{role_name}' does not exist")
    return role


def _parse_csv_optional_float(value: str, field_name: str) -> float | None:
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be numeric") from exc


def _parse_csv_services(value: str) -> list[str]:
    parts = [service.strip().lower() for service in value.split("|") if service.strip()]
    if not parts:
        raise ValueError("services must contain at least one value")
    return parts


def _build_store_create_request_from_csv_row(row: dict[str, str]) -> StoreCreateRequest:
    missing_fields = [field for field in CSV_REQUIRED_FIELDS if not row.get(field, "").strip()]
    if missing_fields:
        raise ValueError(f"missing required field(s): {', '.join(missing_fields)}")

    latitude = _parse_csv_optional_float(row.get("latitude", ""), "latitude")
    longitude = _parse_csv_optional_float(row.get("longitude", ""), "longitude")

    return StoreCreateRequest(
        store_id=row["store_id"],
        name=row["name"],
        store_type=row["store_type"],
        status=row["status"],
        address_street=row["address_street"],
        address_city=row["address_city"],
        address_state=row["address_state"],
        address_postal_code=row["address_postal_code"],
        address_country=row["address_country"],
        phone=row["phone"],
        services=_parse_csv_services(row["services"]),
        hours=StoreHoursCreate(
            mon=row["hours_mon"],
            tue=row["hours_tue"],
            wed=row["hours_wed"],
            thu=row["hours_thu"],
            fri=row["hours_fri"],
            sat=row["hours_sat"],
            sun=row["hours_sun"],
        ),
        latitude=latitude,
        longitude=longitude,
    )


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
def list_stores(
    db: DBSession,
    _current_user=Depends(require_permissions("stores.read")),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="store_id", pattern="^(store_id|name|status|created_at)$"),
    status: StoreStatus | None = Query(default=None),
) -> dict[str, object]:
    """Admin endpoint to list stores with pagination and optional status filtering."""

    filters = []
    if status is not None:
        filters.append(Stores.status == status)

    where_clause = and_(*filters) if filters else None
    sort_column = getattr(Stores, sort_by, Stores.store_id)

    count_query = select(func.count(Stores.store_id))
    data_query = (
        select(Stores)
        .options(selectinload(Stores.services))
        .order_by(sort_column)
        .limit(limit)
        .offset(offset)
    )

    if where_clause is not None:
        count_query = count_query.where(where_clause)
        data_query = data_query.where(where_clause)

    total_count = db.execute(count_query).scalar_one()
    stores = db.execute(data_query).scalars().all()

    page_count = (total_count + limit - 1) // limit if total_count else 0
    current_page = (offset // limit) + 1 if total_count else 0

    return {
        "items": [_store_response(store) for store in stores],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "page_count": page_count,
            "current_page": current_page,
        },
    }

@app.get("/api/admin/stores/{store_id}")
def get_store_by_id(
    db: DBSession,
    _current_user=Depends(require_permissions("stores.read")),
    store_id: str = Path(..., pattern=STORE_ID_PATTERN),
) -> dict[str, object]:
    """Admin endpoint to retrieve a single store by store_id."""
    store = db.execute(
        select(Stores).options(selectinload(Stores.services)).where(Stores.store_id == store_id)
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")
    return _store_response(store)

@app.patch("/api/admin/stores/{store_id}")
def update_store(
    db: DBSession,
    _current_user=Depends(require_permissions("stores.write")),
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
def delete_store(
    db: DBSession,
    _current_user=Depends(require_permissions("stores.write")),
    store_id: str = Path(..., pattern=STORE_ID_PATTERN),
) -> dict[str, str]:
    """Admin endpoint to deactivate a store by ID (soft delete)."""
    store = db.execute(select(Stores).where(Stores.store_id == store_id)).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="Store not found")

    store.status = StoreStatus.INACTIVE
    db.commit()
    clear_search_cache()
    return {"status": StoreStatus.INACTIVE.value, "store_id": store_id}

@app.post("/api/admin/stores", status_code=201)
def create_store(
    payload: StoreCreateRequest,
    db: DBSession,
    _current_user=Depends(require_permissions("stores.write")),
) -> dict[str, object]:
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


@app.post("/api/admin/stores/import")
async def import_stores_csv(
    file: UploadFile = File(...),
    db: DBSession = ...,
    _current_user=Depends(require_permissions("stores.import")),
) -> dict[str, object]:
    """Admin endpoint to import stores from CSV with create/update behavior (upsert)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="CSV file is required")

    raw_bytes = await file.read()
    try:
        text_content = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from exc

    try:
        dataframe = pd.read_csv(
            io.StringIO(text_content),
            dtype=str,
            keep_default_na=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {exc}") from exc

    if list(dataframe.columns) != CSV_IMPORT_HEADERS:
        raise HTTPException(
            status_code=400,
            detail={
                "total_rows_processed": 0,
                "successfully_created": 0,
                "successfully_updated": 0,
                "failed": [
                    {
                        "row_number": 1,
                        "error": (
                            "CSV headers do not match expected format exactly. "
                            f"Expected: {','.join(CSV_IMPORT_HEADERS)}"
                        ),
                    }
                ],
            },
        )

    parsed_rows: list[tuple[int, StoreCreateRequest]] = []
    failed: list[dict[str, object]] = []
    seen_store_ids: set[str] = set()
    geocoded_coordinates: dict[str, tuple[float, float]] = {}
    total_rows_processed = len(dataframe.index)

    for index, row in dataframe.iterrows():
        row_number = index + 2
        row_dict = {column: str(row[column]) for column in CSV_IMPORT_HEADERS}
        try:
            payload = _build_store_create_request_from_csv_row(row_dict)
            if payload.store_id in seen_store_ids:
                raise ValueError(f"duplicate store_id '{payload.store_id}' in CSV")
            seen_store_ids.add(payload.store_id)
            parsed_rows.append((row_number, payload))
        except Exception as exc:
            failed.append({"row_number": row_number, "error": str(exc)})

    for row_number, payload in parsed_rows:
        try:
            geocoded_coordinates[payload.store_id] = _geocode_store_location(payload)
        except Exception as exc:
            failed.append({"row_number": row_number, "error": f"geocoding failed: {exc}"})

    if failed:
        raise HTTPException(
            status_code=400,
            detail={
                "total_rows_processed": total_rows_processed,
                "successfully_created": 0,
                "successfully_updated": 0,
                "failed": failed,
            },
        )

    created = 0
    updated = 0
    try:
        store_ids = [payload.store_id for _, payload in parsed_rows]
        existing_stores = db.execute(
            select(Stores).options(selectinload(Stores.services)).where(Stores.store_id.in_(store_ids))
        ).scalars().all()
        existing_by_id = {store.store_id: store for store in existing_stores}

        for _, payload in parsed_rows:
            latitude, longitude = geocoded_coordinates[payload.store_id]
            store = existing_by_id.get(payload.store_id)

            if store is None:
                store = Stores(store_id=payload.store_id)
                db.add(store)
                existing_by_id[payload.store_id] = store
                created += 1
            else:
                updated += 1

            store.name = payload.name
            store.store_type = payload.store_type
            store.status = payload.status
            store.latitude = latitude
            store.longitude = longitude
            store.address_street = payload.address_street
            store.address_city = payload.address_city
            store.address_state = payload.address_state
            store.address_postal_code = payload.address_postal_code
            store.address_country = payload.address_country
            store.phone = payload.phone
            store.hours_mon = payload.hours.mon
            store.hours_tue = payload.hours.tue
            store.hours_wed = payload.hours.wed
            store.hours_thu = payload.hours.thu
            store.hours_fri = payload.hours.fri
            store.hours_sat = payload.hours.sat
            store.hours_sun = payload.hours.sun

            store.services.clear()
            db.flush()
            store.services.extend(
                StoreService(service_name=service_name) for service_name in payload.services
            )

        db.commit()
        clear_search_cache()
        return {
            "total_rows_processed": total_rows_processed,
            "successfully_created": created,
            "successfully_updated": updated,
            "failed": [],
        }
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail={
                "total_rows_processed": total_rows_processed,
                "successfully_created": 0,
                "successfully_updated": 0,
                "failed": [{"row_number": None, "error": f"import transaction failed: {exc}"}],
            },
        ) from exc


@app.post("/api/admin/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    db: DBSession,
    _current_user=Depends(require_permissions("users.manage")),
) -> dict[str, object]:
    """Admin endpoint to create a user."""
    try:
        role = _get_role_or_404(db, payload.role)
        user = User(
            user_id=payload.user_id,
            email=payload.email,
            password_hash=_hash_password(payload.password),
            role_id=role.id,
            status=payload.status,
            must_change_password=payload.must_change_password,
        )
        db.add(user)
        db.commit()
        created_user = db.execute(
            select(User).options(selectinload(User.role)).where(User.user_id == payload.user_id)
        ).scalar_one()
        return _user_response(created_user)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"User already exists or violates a database constraint: {exc.orig}") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Failed to create user: {exc}") from exc


@app.get("/api/admin/users")
def list_users(
    db: DBSession,
    _current_user=Depends(require_permissions("users.manage")),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    sort_by: str = Query(default="user_id", pattern="^(user_id|email|status|created_at)$"),
    status: UserStatus | None = Query(default=None),
) -> dict[str, object]:
    """Admin endpoint to list users with pagination and optional status filtering."""
    filters = []
    if status is not None:
        filters.append(User.status == status)

    where_clause = and_(*filters) if filters else None
    sort_column = getattr(User, sort_by, User.user_id)

    count_query = select(func.count(User.user_id))
    data_query = (
        select(User)
        .options(selectinload(User.role))
        .order_by(sort_column)
        .limit(limit)
        .offset(offset)
    )

    if where_clause is not None:
        count_query = count_query.where(where_clause)
        data_query = data_query.where(where_clause)

    total_count = db.execute(count_query).scalar_one()
    users = db.execute(data_query).scalars().all()

    page_count = (total_count + limit - 1) // limit if total_count else 0
    current_page = (offset // limit) + 1 if total_count else 0

    return {
        "items": [_user_response(user) for user in users],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "page_count": page_count,
            "current_page": current_page,
        },
    }


@app.put("/api/admin/users/{user_id}")
def update_user(
    payload: UserUpdateRequest,
    db: DBSession,
    _current_user=Depends(require_permissions("users.manage")),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
) -> dict[str, object]:
    """Admin endpoint to update a user's role and/or status."""
    user = db.execute(
        select(User)
        .options(selectinload(User.role), selectinload(User.refresh_tokens))
        .where(User.user_id == user_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.role is not None:
        role = _get_role_or_404(db, payload.role)
        user.role_id = role.id

    if payload.status is not None:
        user.status = payload.status

    if payload.status == UserStatus.INACTIVE:
        revoked_at = datetime.now(timezone.utc)
        for token in user.refresh_tokens:
            if token.revoked_at is None:
                token.revoked_at = revoked_at

    db.commit()
    updated_user = db.execute(
        select(User).options(selectinload(User.role)).where(User.user_id == user_id)
    ).scalar_one_or_none()
    if updated_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_response(updated_user)


@app.delete("/api/admin/users/{user_id}")
def deactivate_user(
    db: DBSession,
    _current_user=Depends(require_permissions("users.manage")),
    user_id: str = Path(..., pattern=USER_ID_PATTERN),
) -> dict[str, str]:
    """Admin endpoint to deactivate a user (soft delete)."""
    user = db.execute(
        select(User).options(selectinload(User.refresh_tokens)).where(User.user_id == user_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.status = UserStatus.INACTIVE
    revoked_at = datetime.now(timezone.utc)
    for token in user.refresh_tokens:
        if token.revoked_at is None:
            token.revoked_at = revoked_at

    db.commit()
    return {"status": UserStatus.INACTIVE.value, "user_id": user_id}
    
    