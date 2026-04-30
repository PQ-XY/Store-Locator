"""Tests for store management endpoints."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Stores, StoreStatus, StoreType


SAMPLE_STORE = {
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
}


class TestStoreCreate:
    """Test store creation."""

    def test_create_store_success(self, client: TestClient, admin_token: str):
        """Test successful store creation."""
        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=SAMPLE_STORE,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["store_id"] == "S0001"
        assert data["name"] == "Test Store"
        assert data["status"] == "active"

    def test_create_store_invalid_phone(self, client: TestClient, admin_token: str):
        """Test store creation with invalid phone."""
        store = SAMPLE_STORE.copy()
        store["store_id"] = "S0002"
        store["phone"] = "invalid-phone"

        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=store,
        )

        assert response.status_code == 422

    def test_create_store_missing_coordinates_auto_geocode(
        self, client: TestClient, admin_token: str
    ):
        """Test store creation without coordinates triggers auto-geocoding."""
        store = SAMPLE_STORE.copy()
        store["store_id"] = "S0003"
        del store["latitude"]
        del store["longitude"]

        response = client.post(
            "/api/admin/stores",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=store,
        )

        assert response.status_code == 201
        data = response.json()
        assert data["latitude"] is not None
        assert data["longitude"] is not None


class TestStoreRead:
    """Test store retrieval."""

    def test_get_stores_list(self, client: TestClient, admin_token: str, db_session: Session):
        """Test listing stores."""
        # Create a test store
        store = Stores(
            store_id="S1000",
            name="List Test Store",
            store_type=StoreType.REGULAR,
            address_street="789 Elm St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-1000",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        db_session.add(store)
        db_session.commit()

        response = client.get(
            "/api/admin/stores?limit=10&offset=0",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert data["pagination"]["total_count"] >= 1

    def test_get_store_by_id(self, client: TestClient, admin_token: str, db_session: Session):
        """Test retrieving specific store."""
        store = Stores(
            store_id="S1001",
            name="Get Test Store",
            store_type=StoreType.REGULAR,
            address_street="456 Oak Ave",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-1001",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        db_session.add(store)
        db_session.commit()

        response = client.get(
            "/api/admin/stores/S1001",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["store_id"] == "S1001"
        assert data["name"] == "Get Test Store"

    def test_get_nonexistent_store(self, client: TestClient, admin_token: str):
        """Test retrieving non-existent store."""
        response = client.get(
            "/api/admin/stores/S9999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 404


class TestStoreUpdate:
    """Test store updates."""

    def test_update_store_name(self, client: TestClient, admin_token: str, db_session: Session):
        """Test updating store name."""
        store = Stores(
            store_id="S1002",
            name="Original Name",
            store_type=StoreType.REGULAR,
            address_street="111 Pine St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-1002",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        db_session.add(store)
        db_session.commit()

        response = client.patch(
            "/api/admin/stores/S1002",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"name": "Updated Name"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    def test_update_store_immutable_field_rejected(
        self, client: TestClient, admin_token: str, db_session: Session
    ):
        """Test that immutable fields cannot be updated."""
        store = Stores(
            store_id="S1003",
            name="Immutable Test",
            store_type=StoreType.REGULAR,
            address_street="222 Oak St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-1003",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        db_session.add(store)
        db_session.commit()

        response = client.patch(
            "/api/admin/stores/S1003",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"store_id": "S0000"},
        )

        assert response.status_code == 422


class TestStoreDelete:
    """Test store deletion."""

    def test_delete_store_soft_delete(
        self, client: TestClient, admin_token: str, db_session: Session
    ):
        """Test soft-deleting a store."""
        store = Stores(
            store_id="S1004",
            name="Delete Test",
            store_type=StoreType.REGULAR,
            address_street="333 Elm St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-1004",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        db_session.add(store)
        db_session.commit()

        response = client.delete(
            "/api/admin/stores/S1004",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        assert response.status_code == 200

        # Verify store is marked inactive
        deleted_store = db_session.query(Stores).filter(Stores.store_id == "S1004").first()
        assert deleted_store.status == StoreStatus.INACTIVE
