"""Tests for search endpoint."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Stores, StoreStatus, StoreType, StoreService


class TestSearchByAddress:
    """Test search by address."""

    def setup_method(self, method, db_session: Session = None):
        """Setup test stores (note: pytest-alembic would handle this better)."""
        pass

    def test_search_by_address(self, client: TestClient, db_session: Session):
        """Test searching by address."""
        # Create test stores
        store = Stores(
            store_id="S2000",
            name="Search Test Store",
            store_type=StoreType.REGULAR,
            address_street="100 Cambridge St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-2000",
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

        response = client.post(
            "/api/stores/search?address=Boston,MA&radius_miles=10"
        )

        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "search_metadata" in data

    def test_search_by_postal_code(self, client: TestClient, db_session: Session):
        """Test searching by postal code."""
        store = Stores(
            store_id="S2001",
            name="Postal Search Store",
            store_type=StoreType.REGULAR,
            address_street="200 Hanover St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02113",
            address_country="USA",
            latitude=42.3629,
            longitude=-71.0527,
            phone="617-555-2001",
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

        response = client.post(
            "/api/stores/search?postal_code=02113&radius_miles=5"
        )

        assert response.status_code == 200

    def test_search_by_coordinates(self, client: TestClient, db_session: Session):
        """Test searching by latitude and longitude."""
        store = Stores(
            store_id="S2002",
            name="Coordinate Search Store",
            store_type=StoreType.REGULAR,
            address_street="300 Congress St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02210",
            address_country="USA",
            latitude=42.3584,
            longitude=-71.0596,
            phone="617-555-2002",
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

        response = client.post(
            "/api/stores/search?latitude=42.3584&longitude=-71.0596&radius_miles=10"
        )

        assert response.status_code == 200


class TestSearchFiltering:
    """Test search filters."""

    def test_search_with_radius(self, client: TestClient, db_session: Session):
        """Test search with different radius values."""
        store = Stores(
            store_id="S2003",
            name="Radius Test",
            store_type=StoreType.REGULAR,
            address_street="400 Tremont St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02116",
            address_country="USA",
            latitude=42.3477,
            longitude=-71.0685,
            phone="617-555-2003",
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

        # Test with small radius
        response = client.post(
            "/api/stores/search?address=Boston,MA&radius_miles=1"
        )
        assert response.status_code == 200

    def test_search_with_invalid_radius(self, client: TestClient):
        """Test search with invalid radius."""
        response = client.post(
            "/api/stores/search?address=Boston,MA&radius_miles=200"
        )

        assert response.status_code == 422  # Out of range

    def test_search_missing_location(self, client: TestClient):
        """Test search without location parameter."""
        response = client.post("/api/stores/search?radius_miles=10")

        assert response.status_code == 422


class TestSearchServices:
    """Test search with service filtering."""

    def test_search_by_service(self, client: TestClient, db_session: Session):
        """Test filtering search by service."""
        store = Stores(
            store_id="S2004",
            name="Pharmacy Store",
            store_type=StoreType.REGULAR,
            address_street="500 Boylston St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02116",
            address_country="USA",
            latitude=42.3468,
            longitude=-71.0720,
            phone="617-555-2004",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status=StoreStatus.ACTIVE,
        )
        service = StoreService(service_name="pharmacy")
        store.services.append(service)
        db_session.add(store)
        db_session.commit()

        response = client.post(
            "/api/stores/search?address=Boston,MA&services=pharmacy&radius_miles=10"
        )

        assert response.status_code == 200

    def test_search_invalid_service(self, client: TestClient):
        """Test search with invalid service."""
        response = client.post(
            "/api/stores/search?address=Boston,MA&services=invalid_service"
        )

        assert response.status_code == 400
        assert "Unsupported service" in response.json()["detail"]


class TestSearchStoreTypes:
    """Test search with store type filtering."""

    def test_search_by_store_type(self, client: TestClient, db_session: Session):
        """Test filtering search by store type."""
        store = Stores(
            store_id="S2005",
            name="Flagship Store",
            store_type=StoreType.FLAGSHIP,
            address_street="600 Washington St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02108",
            address_country="USA",
            latitude=42.3563,
            longitude=-71.0620,
            phone="617-555-2005",
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

        response = client.post(
            "/api/stores/search?address=Boston,MA&store_types=flagship&radius_miles=10"
        )

        assert response.status_code == 200
