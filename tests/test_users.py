"""Tests for user management endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User, UserStatus, Role


class TestUserCreate:
    """Test user creation endpoint."""

    def test_create_user_admin(self, client: TestClient, admin_token: str, db_session: Session):
        """Test admin can create users."""
        response = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": "U100",
                "email": "newuser@test.com",
                "password": "NewPass123!",
                "role": "viewer",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["user_id"] == "U100"
        assert data["email"] == "newuser@test.com"
        assert data["status"] == "active"
        assert "password_hash" not in data

    def test_create_user_marketer_denied(self, client: TestClient, marketer_token: str):
        """Test marketer cannot create users."""
        response = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {marketer_token}"},
            json={
                "user_id": "U101",
                "email": "another@test.com",
                "password": "AnotherPass123!",
                "role": "viewer",
            },
        )

        assert response.status_code == 403

    def test_create_user_invalid_role(self, client: TestClient, admin_token: str):
        """Test create user with invalid role."""
        response = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": "U102",
                "email": "invalid@test.com",
                "password": "ValidPass123!",
                "role": "superadmin",
            },
        )

        assert response.status_code == 400
        assert "does not exist" in response.json()["detail"].lower()

    def test_create_user_weak_password(self, client: TestClient, admin_token: str):
        """Test create user with weak password."""
        response = client.post(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "user_id": "U103",
                "email": "weak@test.com",
                "password": "weak",
                "role": "viewer",
            },
        )

        assert response.status_code == 422


class TestUserList:
    """Test user list endpoint."""

    def test_list_users(self, client: TestClient, admin_token: str):
        """Test listing users with pagination."""
        response = client.get(
            "/api/admin/users?limit=10&offset=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["pagination"]["total_count"] >= 3
        assert isinstance(data["items"], list)

    def test_list_users_filter_status(self, client: TestClient, admin_token: str):
        """Test list users with status filter."""
        response = client.get(
            "/api/admin/users?status=active",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        for user in data["items"]:
            assert user["status"] == "active"


class TestUserUpdate:
    """Test user update endpoint."""

    def test_update_user_role(self, client: TestClient, admin_token: str, db_session: Session):
        """Test updating user role."""
        # Create a user first
        user = User(
            user_id="U104",
            email="toupdate@test.com",
            password_hash="hashed",
            status=UserStatus.ACTIVE,
        )
        viewer_role = db_session.query(Role).filter(Role.name == "viewer").first()
        user.role = viewer_role
        db_session.add(user)
        db_session.commit()

        response = client.put(
            "/api/admin/users/U104",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role": "marketer"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "U104"

    def test_update_user_status(self, client: TestClient, admin_token: str, db_session: Session):
        """Test updating user status."""
        user = User(
            user_id="U105",
            email="todeactivate@test.com",
            password_hash="hashed",
            status=UserStatus.ACTIVE,
        )
        viewer_role = db_session.query(Role).filter(Role.name == "viewer").first()
        user.role = viewer_role
        db_session.add(user)
        db_session.commit()

        response = client.put(
            "/api/admin/users/U105",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"status": "inactive"},
        )

        assert response.status_code == 200

    def test_update_nonexistent_user(self, client: TestClient, admin_token: str):
        """Test updating non-existent user."""
        response = client.put(
            "/api/admin/users/U999",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"role": "viewer"},
        )

        assert response.status_code == 404


class TestUserDelete:
    """Test user delete endpoint."""

    def test_delete_user(self, client: TestClient, admin_token: str, db_session: Session):
        """Test soft-deleting a user."""
        user = User(
            user_id="U106",
            email="todelete@test.com",
            password_hash="hashed",
            status=UserStatus.ACTIVE,
        )
        viewer_role = db_session.query(Role).filter(Role.name == "viewer").first()
        user.role = viewer_role
        db_session.add(user)
        db_session.commit()

        response = client.delete(
            "/api/admin/users/U106",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200

        # Verify user is marked inactive
        deleted_user = db_session.query(User).filter(User.user_id == "U106").first()
        assert deleted_user.status == UserStatus.INACTIVE
