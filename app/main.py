from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.database import DBSession, create_db_and_tables
from app.redis import check_redis_connection
from app.search import router as search_router


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