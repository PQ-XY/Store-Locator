import json
import logging
import os
from typing import Any

import redis
from dotenv import load_dotenv

load_dotenv()

# Redis configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
CACHE_EVENTS_ENABLED = os.getenv("CACHE_EVENTS_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

logger = logging.getLogger(__name__)
# Ensure logger outputs to console with proper handler
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Initialize Redis connection pool
redis_client = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_keepalive=True,
)


def check_redis_connection() -> bool:
    """Check if Redis is accessible."""
    try:
        redis_client.ping()
        return True
    except redis.ConnectionError:
        return False


# Geocoding cache operations
GEOCODE_CACHE_KEY_PREFIX = "geocode:"


def _emit_cache_event(operation: str, cache_key: str) -> None:
    """Emit cache operation notifications when enabled."""
    if not CACHE_EVENTS_ENABLED:
        return
    logger.info("[cache] %s %s", operation, cache_key)


def cache_set_geocode(key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    """Store geocoding result in Redis."""
    cache_key = f"{GEOCODE_CACHE_KEY_PREFIX}{key}"
    redis_client.setex(cache_key, ttl_seconds, json.dumps(value))
    _emit_cache_event("set", cache_key)


def cache_get_geocode(key: str) -> dict[str, Any] | None:
    """Retrieve geocoding result from Redis."""
    cache_key = f"{GEOCODE_CACHE_KEY_PREFIX}{key}"
    data = redis_client.get(cache_key)
    if data is None:
        _emit_cache_event("miss", cache_key)
        return None
    _emit_cache_event("hit", cache_key)
    return json.loads(data)


# Search cache operations
SEARCH_CACHE_KEY_PREFIX = "search:"


def cache_set_search(key: str, value: dict[str, Any], ttl_seconds: int) -> None:
    """Store search result in Redis."""
    cache_key = f"{SEARCH_CACHE_KEY_PREFIX}{key}"
    redis_client.setex(cache_key, ttl_seconds, json.dumps(value))
    _emit_cache_event("set", cache_key)


def cache_get_search(key: str) -> dict[str, Any] | None:
    """Retrieve search result from Redis."""
    cache_key = f"{SEARCH_CACHE_KEY_PREFIX}{key}"
    data = redis_client.get(cache_key)
    if data is None:
        _emit_cache_event("miss", cache_key)
        return None
    _emit_cache_event("hit", cache_key)
    return json.loads(data)


def clear_search_cache() -> None:
    """Remove all cached search results."""
    for key in redis_client.scan_iter(f"{SEARCH_CACHE_KEY_PREFIX}*"):
        redis_client.delete(key)
        _emit_cache_event("clear", key)


# Rate limiting operations
RATE_LIMIT_KEY_PREFIX = "rate_limit:"


def rate_limit_increment(ip_address: str) -> None:
    """Increment request count for IP address with 1-hour expiration."""
    key = f"{RATE_LIMIT_KEY_PREFIX}{ip_address}"
    # Use INCR with EXPIRE to ensure cleanup
    pipe = redis_client.pipeline()
    pipe.incr(key)
    pipe.expire(key, 3600)  # 1 hour
    pipe.execute()


def rate_limit_get_count(ip_address: str) -> int:
    """Get current request count for IP address."""
    key = f"{RATE_LIMIT_KEY_PREFIX}{ip_address}"
    count = redis_client.get(key)
    return int(count) if count is not None else 0


def rate_limit_get_ttl(ip_address: str) -> int:
    """Get remaining TTL for IP address rate limit in seconds."""
    key = f"{RATE_LIMIT_KEY_PREFIX}{ip_address}"
    ttl = redis_client.ttl(key)
    return max(ttl, 0)  # Return 0 if key doesn't exist or has no expiration


# Minute-window rate limiting
RATE_LIMIT_MINUTE_KEY_PREFIX = "rate_limit_min:"


def rate_limit_minute_check(ip_address: str, limit: int, window_seconds: int = 60) -> bool:
    """
    Check if IP is within minute rate limit using sliding window.
    Returns True if under limit, False if exceeded.
    """
    key = f"{RATE_LIMIT_MINUTE_KEY_PREFIX}{ip_address}"
    current_time = int(redis_client.time()[0])  # Get server time

    # Remove old entries outside the window
    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, current_time - window_seconds)
    pipe.zadd(key, {str(current_time): current_time})  # Add current request
    pipe.zcard(key)  # Get count
    pipe.expire(key, window_seconds)  # Set expiration
    results = pipe.execute()

    count = results[2]
    return count <= limit


def rate_limit_minute_get_reset_time(ip_address: str, window_seconds: int = 60) -> int:
    """Get the reset time (seconds until rate limit resets) for minute window."""
    key = f"{RATE_LIMIT_MINUTE_KEY_PREFIX}{ip_address}"
    ttl = redis_client.ttl(key)
    if ttl == -1:  # Key exists but has no expiration (shouldn't happen)
        return window_seconds
    if ttl == -2:  # Key doesn't exist
        return 0
    return ttl


# Hour-window rate limiting
RATE_LIMIT_HOUR_KEY_PREFIX = "rate_limit_hour:"


def rate_limit_hour_check(ip_address: str, limit: int, window_seconds: int = 3600) -> bool:
    """
    Check if IP is within hour rate limit using sliding window.
    Returns True if under limit, False if exceeded.
    """
    key = f"{RATE_LIMIT_HOUR_KEY_PREFIX}{ip_address}"
    current_time = int(redis_client.time()[0])  # Get server time

    # Remove old entries outside the window
    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, current_time - window_seconds)
    pipe.zadd(key, {str(current_time): current_time})  # Add current request
    pipe.zcard(key)  # Get count
    pipe.expire(key, window_seconds)  # Set expiration
    results = pipe.execute()

    count = results[2]
    return count <= limit


def rate_limit_hour_get_reset_time(ip_address: str, window_seconds: int = 3600) -> int:
    """Get the reset time (seconds until rate limit resets) for hour window."""
    key = f"{RATE_LIMIT_HOUR_KEY_PREFIX}{ip_address}"
    ttl = redis_client.ttl(key)
    if ttl == -1:  # Key exists but has no expiration (shouldn't happen)
        return window_seconds
    if ttl == -2:  # Key doesn't exist
        return 0
    return ttl
