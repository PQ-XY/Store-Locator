# Redis Migration: Caching & Rate Limiting

## Summary
Successfully migrated from in-memory storage to Redis-backed caching and rate limiting for the store search endpoint.

## What Changed

### 1. New Redis Module (`app/redis.py`)
- **Geocoding Cache**: Stores address/postal code → coordinates mappings (30-day TTL)
- **Search Cache**: Stores complete search results (10-minute TTL)
- **Rate Limiting (Minute Window)**: Sliding window using Redis sorted sets (10 requests/min per IP)
- **Rate Limiting (Hour Window)**: Sliding window using Redis sorted sets (100 requests/hour per IP)

All functions use Redis key prefixes for organization:
- `geocode:` for geocoding results
- `search:` for search results
- `rate_limit_min:` for per-minute limits
- `rate_limit_hour:` for per-hour limits

### 2. Updated `app/search.py`
Removed:
- In-memory dictionaries: `GEOCODE_CACHE`, `SEARCH_CACHE`, `RATE_LIMIT_EVENTS`
- Threading locks: `CACHE_LOCK`, `RATE_LIMIT_LOCK`
- In-memory cache entry dataclasses
- Manual TTL expiration logic

Replaced with:
- Redis client function calls (`cache_get_*`, `cache_set_*`, `rate_limit_*_check`)
- Cleaner rate limit enforcement in `enforce_search_rate_limit()`
- Geocoding cache stored as JSON in Redis

### 3. Updated `app/main.py`
Added:
- Redis health check on startup: `check_redis_connection()`
- Warning if Redis is unavailable
- New `/health/redis` endpoint to check Redis status

### 4. Dependencies
- Added `redis>=5.0` to `requirements.txt`
- Installed via: `pip install redis`

### 5. Environment Configuration
Updated `.env.example` with Redis defaults:
```
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
```

All Redis settings have sensible defaults - no configuration required for local development.

## Setup

### Prerequisites
1. Redis server running locally:
   ```bash
   brew services start redis
   ```
   Or verify with:
   ```bash
   redis-cli ping  # Should return PONG
   ```

2. Python redis client:
   ```bash
   pip install redis
   ```

### Verify Installation
```bash
# Check Redis is accessible
redis-cli ping

# Run search endpoint to populate cache
cd /path/to/project
python -c "
from app.search import search_stores, StoreSearchRequest
from app.database import SessionLocal
from sqlalchemy import text

with SessionLocal() as db:
    row = db.execute(text('SELECT latitude, longitude FROM stores LIMIT 1')).mappings().first()
    result = search_stores(
        payload=StoreSearchRequest(latitude=row['latitude'], longitude=row['longitude']),
        db=db,
        radius_miles=25
    )
    print(f'✓ Search returned {len(result.results)} results')

# Verify cache
"

# Check Redis keys
redis-cli KEYS "*"
```

## Performance Benefits

| Metric | Before (In-Memory) | After (Redis) |
|--------|-------------------|---------------|
| Horizontal Scaling | ✗ Not possible | ✓ Shared across instances |
| Persistence | ✗ Lost on restart | ✓ Configurable |
| Memory Isolation | ✗ Shared process | ✓ Separate service |
| Multi-Instance | ✗ Separate caches | ✓ Single shared cache |
| Observability | ✗ Limited | ✓ `redis-cli` tools |

## Rate Limiting Details

### Per-IP Rate Limits
- **10 requests/minute**: Returns HTTP 429 with `Retry-After` header
- **100 requests/hour**: Returns HTTP 429 with `Retry-After` header

### Implementation
Uses Redis sorted sets with server timestamps:
```python
# Each IP gets a sorted set: rate_limit_min:127.0.0.1
# Members are timestamps of requests, scores are also timestamps
# Sliding window removes old entries > 60 seconds old
# New requests increment count and check limit
```

## API Health Endpoints

### Check all services
```bash
# Database
curl http://localhost:8000/health/db

# Redis  
curl http://localhost:8000/health/redis

# Overall
curl http://localhost:8000/health
```

## Troubleshooting

### Redis Connection Error
```
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```
**Solution**: Start Redis with `brew services start redis`

### Cache Not Persisting
- Check Redis is running: `redis-cli ping`
- Verify REDIS_* environment variables if using non-default settings
- View cache keys: `redis-cli KEYS "*"`

### Clear All Cache
```bash
redis-cli FLUSHDB   # Clear current DB
redis-cli FLUSHALL  # Clear all DBs
```

## Configuration for Production

For production deployments with multiple app instances:

```env
# Use external Redis instance
REDIS_HOST=redis-cluster.internal
REDIS_PORT=6380
REDIS_DB=0

# Optional: Add Redis auth
# REDIS_PASSWORD=secret
```

Update `app/redis.py` to add password support if needed:
```python
redis_client = redis.Redis(
    ...,
    password=os.getenv("REDIS_PASSWORD"),
)
```

## Files Modified
- `app/redis.py` (new)
- `app/search.py`
- `app/main.py`
- `requirements.txt`
- `.env.example`
