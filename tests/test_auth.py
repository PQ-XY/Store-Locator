"""Tests for authentication endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import _hash_password
from app.models import User, UserStatus, RoleStatus


class TestAuthLogin:
    """Test login endpoint."""

    def test_login_success(self, client: TestClient, db_session: Session):
        """Test successful login returns tokens."""
        user = User(
            user_id="U999",
            email="test@example.com",
            password_hash=_hash_password("TestPass123!"),
            status=UserStatus.ACTIVE,
        )
        admin_role = db_session.query(User).filter(User.email == "admin@test.com").first().role
        user.role = admin_role
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "password": "TestPass123!"},
        )

        assert response.status_code == 200, f"Login failed: {response.json()}"
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["access_token_expires_in"] > 0

    def test_login_invalid_email(self, client: TestClient):
        """Test login with non-existent email."""
        response = client.post(
            "/api/auth/login",
            json={"email": "nonexistent@example.com", "password": "AnyPass123!"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid email or password"

    def test_login_wrong_password(self, client: TestClient, db_session: Session):
        """Test login with wrong password."""
        user = User(
            user_id="U998",
            email="test2@example.com",
            password_hash=_hash_password("CorrectPass123!"),
            status=UserStatus.ACTIVE,
        )
        admin_role = db_session.query(User).filter(User.email == "admin@test.com").first().role
        user.role = admin_role
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/auth/login",
            json={"email": "test2@example.com", "password": "WrongPass123!"},
        )

        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

    def test_login_inactive_user(self, client: TestClient, db_session: Session):
        """Test login with inactive user."""
        user = User(
            user_id="U997",
            email="inactive@example.com",
            password_hash=_hash_password("ValidPass123!"),
            status=UserStatus.INACTIVE,
        )
        admin_role = db_session.query(User).filter(User.email == "admin@test.com").first().role
        user.role = admin_role
        db_session.add(user)
        db_session.commit()

        response = client.post(
            "/api/auth/login",
            json={"email": "inactive@example.com", "password": "ValidPass123!"},
        )

        assert response.status_code == 401
        # Endpoint returns generic message for security (doesn't reveal user exists but is inactive)
        assert "invalid" in response.json()["detail"].lower()


class TestAuthRefresh:
    """Test refresh token endpoint."""

    def test_refresh_success(self, client: TestClient, admin_refresh_token: str):
        """Test successful token refresh."""
        response = client.post(
            "/api/auth/refresh",
            json={"refresh_token": admin_refresh_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["access_token_expires_in"] > 0

    def test_refresh_missing_token(self, client: TestClient):
        """Test refresh without token."""
        response = client.post("/api/auth/refresh")

        assert response.status_code == 422

    def test_refresh_invalid_token(self, client: TestClient):
        """Test refresh with invalid token."""
        response = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "invalid_token"},
        )

        assert response.status_code == 401


class TestAuthLogout:
    """Test logout endpoint."""

    def test_logout_success(self, client: TestClient, admin_refresh_token: str):
        """Test successful logout."""
        response = client.post(
            "/api/auth/logout",
            json={"refresh_token": admin_refresh_token},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "logged_out"

    def test_logout_missing_token(self, client: TestClient):
        """Test logout without token."""
        response = client.post("/api/auth/logout")

        assert response.status_code == 422


class TestAuthPermissions:
    """Test permission-based access control."""

    def test_admin_can_write_stores(self, client: TestClient, admin_token: str):
        """Test admin can access stores.write permission."""
        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "store_id": "S0001",
                "name": "Test Store",
                "store_type": "flagship",
                "address_street": "123 Main St",
                "address_city": "Boston",
                "address_state": "MA",
                "address_postal_code": "02114",
                "address_country": "USA",
                "latitude": 42.3601,
                "longitude": -71.0589,
                "phone": "617-555-0000",
                "hours": {
                    "mon": "08:00-22:00",
                    "tue": "08:00-22:00",
                    "wed": "08:00-22:00",
                    "thu": "08:00-22:00",
                    "fri": "08:00-22:00",
                    "sat": "09:00-21:00",
                    "sun": "10:00-20:00",
                },
            },
        )

        assert response.status_code == 201

    def test_marketer_can_write_stores(self, client: TestClient, marketer_token: str):
        """Test marketer can write stores."""
        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {marketer_token}"},
            json={
                "store_id": "S0002",
                "name": "Marketer Store",
                "store_type": "regular",
                "address_street": "456 Oak Ave",
                "address_city": "Boston",
                "address_state": "MA",
                "address_postal_code": "02114",
                "address_country": "USA",
                "latitude": 42.3601,
                "longitude": -71.0589,
                "phone": "617-555-0001",
                "hours": {
                    "mon": "08:00-22:00",
                    "tue": "08:00-22:00",
                    "wed": "08:00-22:00",
                    "thu": "08:00-22:00",
                    "fri": "08:00-22:00",
                    "sat": "09:00-21:00",
                    "sun": "10:00-20:00",
                },
            },
        )

        assert response.status_code == 201

    def test_viewer_cannot_write_stores(self, client: TestClient, viewer_token: str):
        """Test viewer cannot write stores."""
        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {viewer_token}"},
            json={
                "store_id": "S0003",
                "name": "Viewer Store",
                "store_type": "regular",
                "address_street": "789 Pine Rd",
                "address_city": "Boston",
                "address_state": "MA",
                "address_postal_code": "02114",
                "address_country": "USA",
                "latitude": 42.3601,
                "longitude": -71.0589,
                "phone": "617-555-0002",
                "hours_mon": "08:00-22:00",
                "hours_tue": "08:00-22:00",
                "hours_wed": "08:00-22:00",
                "hours_thu": "08:00-22:00",
                "hours_fri": "08:00-22:00",
                "hours_sat": "09:00-21:00",
                "hours_sun": "10:00-20:00",
            },
        )

        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()

    def test_viewer_can_read_stores(self, client: TestClient, viewer_token: str):
        """Test viewer can read stores."""
        response = client.get(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {viewer_token}"},
        )

        assert response.status_code == 200
