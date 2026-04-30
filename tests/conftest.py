"""Pytest configuration and shared fixtures for Store Locator tests."""
import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Use a shared in-memory SQLite database for isolated tests.
TEST_SQLALCHEMY_DATABASE_URL = "sqlite://"

# Import after setting environment
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345-67890-abcdef")

# IMPORTANT: Import app.database BEFORE app.main so we can patch the engine
import app.database
from app.database import Base, get_db
from app.main import app, _hash_password
from app.models import Role, Permission, PermissionStatus, RoleStatus, User, UserStatus
from app.seed_users import seed_permissions, seed_role_permissions, seed_roles, seed_users


@pytest.fixture(scope="function")
def test_engine():
    """Create test database engine fresh for each test."""
    engine = create_engine(
        TEST_SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator[Session, None, None]:
    """Create fresh database session for each test with all seeded data."""
    session = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)()

    # Seed base data
    roles_by_name = seed_roles(session)
    permissions_by_code = seed_permissions(session)
    seed_role_permissions(session, roles_by_name, permissions_by_code)
    seed_users(session, roles_by_name)
    session.commit()

    # Override the database dependency
    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    yield session

    session.close()
    test_engine.dispose()
    app.dependency_overrides.clear()


@pytest.fixture
def client(db_session) -> TestClient:
    """Create test client with overridden database."""
    return TestClient(app)


@pytest.fixture
def admin_token(client: TestClient, db_session: Session) -> str:
    """Get seeded admin user and return access token."""
    # Seeded users use DEFAULT_PASSWORD from app/seed_users.py
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200, f"Admin login failed: {response.json()}"
    return response.json()["access_token"]


@pytest.fixture
def admin_refresh_token(client: TestClient, db_session: Session) -> str:
    """Get seeded admin user refresh token."""
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@test.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200, f"Admin login failed: {response.json()}"
    return response.json()["refresh_token"]


@pytest.fixture
def marketer_token(client: TestClient, db_session: Session) -> str:
    """Get seeded marketer user and return access token."""
    # Seeded users use DEFAULT_PASSWORD from app/seed_users.py
    response = client.post(
        "/api/auth/login",
        json={"email": "marketer@test.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200, f"Marketer login failed: {response.json()}"
    return response.json()["access_token"]


@pytest.fixture
def viewer_token(client: TestClient, db_session: Session) -> str:
    """Get seeded viewer user and return access token."""
    # Seeded users use DEFAULT_PASSWORD from app/seed_users.py
    response = client.post(
        "/api/auth/login",
        json={"email": "viewer@test.com", "password": "TestPassword123!"},
    )
    assert response.status_code == 200, f"Viewer login failed: {response.json()}"
    return response.json()["access_token"]
