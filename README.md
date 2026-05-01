# Store Locator API

Production-style FastAPI service for a multi-location retail business with:
- Public store search
- Authenticated admin APIs for store and user management
- Batch CSV import with validation and upsert behavior
- JWT auth with access and refresh tokens
- Redis-backed caching and rate limiting

## Project Description

This project provides two API surfaces:

1. Public search API (`/api/stores/search`)
- Search by address, postal code, or coordinates
- Filter by radius, services, store types, and open-now status
- Returns results sorted by distance

2. Internal admin API (`/api/admin/*`)
- Store CRUD (soft delete)
- Batch CSV import (create/update)
- User management with role-based access control (RBAC)

Authentication and authorization are implemented with JWT tokens and DB-backed role/permission checks.

## Framework Choice

### Why FastAPI
- Typed request/response models with Pydantic
- Built-in OpenAPI/Swagger docs (`/docs`)
- Dependency injection for DB sessions and permission guards
- Good fit for REST APIs with validation-heavy payloads

### Core Stack
- FastAPI + Uvicorn
- SQLAlchemy 2.x ORM
- PostgreSQL (primary datastore)
- Redis (rate limiting + cache)
- Pydantic (validation)
- PyJWT + bcrypt (auth)

## CSV Processing Choice (pandas)

This project uses `pandas` for CSV ingestion in both:
- API import endpoint (`POST /api/admin/stores/import`)
- CLI import utility (`python -m app.import_stores <csv_file>`)

### Why pandas instead of built-in `csv`
- Simpler header/column validation
- Reliable string typing (`dtype=str`) to avoid accidental type coercion
- Easier row-level normalization/validation pipeline
- Cleaner handling for larger CSV datasets (50 and 1000 row samples)

## Setup Instructions

## 1. Prerequisites
- Python 3.11+ (project currently runs in local venv)
- PostgreSQL running locally
- Redis running locally (recommended; app can still start without Redis)

## 2. Clone and install dependencies

```bash
git clone <your-repo-url>
cd "Store Locator"
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Configure environment

Create `.env` from `.env.example`:

```bash
cp .env.example .env
```

Update at least:

```env
DATABASE_URL=postgresql+psycopg://<db_user>:<db_password>@localhost:5432/<db_name>
JWT_SECRET=<long-random-secret>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# Optional Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

## 4. Prepare database

Create your PostgreSQL database (example):

```bash
createdb store_locator
```

Then seed roles/permissions/default users:

```bash
python -m app.seed_users
```

Default seeded users (local dev):
- `admin@test.com`
- `marketer@test.com`
- `viewer@test.com`

Default password for all seeded users:
- `TestPassword123!`

## 5. (Optional) Seed stores from CSV

```bash
python -m app.import_stores stores_50.csv
```

## How to Run Locally

From project root:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

App will run at:
- `http://127.0.0.1:8000`

Health endpoints:
- `GET /health`
- `GET /health/db`
- `GET /health/redis`

## How to Run Tests

Run full test suite:

```bash
source .venv/bin/activate
pytest tests/ -q
```

Run specific file:

```bash
pytest tests/test_auth.py -v
```

Current status in this workspace:
- 49 tests passing

## API Documentation

When the app is running:
- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

## API Endpoint Summary

### Auth (`/api/auth`)
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `POST /api/auth/logout`

### Public Search (`/api/stores`)
- `POST /api/stores/search`

### Admin Stores (`/api/admin/stores`)
- `GET /api/admin/stores`
- `GET /api/admin/stores/{store_id}`
- `POST /api/admin/stores`
- `PATCH /api/admin/stores/{store_id}`
- `DELETE /api/admin/stores/{store_id}`
- `POST /api/admin/stores/import`

### Admin Users (`/api/admin/users`)
- `POST /api/admin/users`
- `GET /api/admin/users`
- `PUT /api/admin/users/{user_id}`
- `DELETE /api/admin/users/{user_id}`

## Authentication Flow Explanation

This project implements a two-token JWT pattern.

1. Login
- Client calls `POST /api/auth/login` with email/password
- Server validates credentials and role/user status
- Server returns:
  - access token (short-lived)
  - refresh token (long-lived)

2. Access protected endpoints
- Client sends access token in `Authorization: Bearer <token>`
- Backend resolves current user from token and checks role/permission

3. Refresh access token
- Client calls `POST /api/auth/refresh` with JSON body:
  - `{ "refresh_token": "..." }`
- Server validates token signature, type, revocation state, and expiry
- Server returns a new access token

4. Logout/revoke
- Client calls `POST /api/auth/logout` with refresh token
- Server marks token as revoked in DB

### RBAC Model
- `admin`: full access, including user management
- `marketer`: store management/import, no user management
- `viewer`: read-only store access

Permissions are resolved from DB (`roles`, `permissions`, `role_permissions`) on request.

## Distance Calculation Method Explanation

Search uses a two-step geospatial strategy for performance and accuracy:

1. Bounding box pre-filter (SQL)
- Compute min/max lat/lon around search center using radius
- Filter candidate stores in SQL with `latitude BETWEEN ...` and `longitude BETWEEN ...`
- This reduces rows before expensive exact distance checks

2. Exact distance calculation
- For filtered candidates, calculate geodesic distance using `geopy.distance.geodesic`
- Keep stores within requested radius
- Sort by `distance_miles` ascending

This approach balances speed (coarse DB filter) and precision (exact geodesic distance).

## Deployment Information

No production deployment metadata is committed in this repository.

Fill in this section when deployed:

- Platform: `<Render | Railway | Heroku | etc.>`
- Base URL: `<https://your-app-url>`
- Swagger URL: `<https://your-app-url/docs>`
- Environment: `<dev/staging/prod>`
- Demo credentials:
  - Admin: `<email/password>`
  - Marketer: `<email/password>`
  - Viewer: `<email/password>`

If deploying publicly, rotate local default credentials and use secure secrets.

## Notes

- Redis is used for search/geocode caching and search rate limiting.
- If Redis is unavailable, app startup prints a warning and health endpoint reflects status.
- Store import endpoint is all-or-nothing: validation/geocoding failures return detailed row-level errors.
