"""Tests for CSV import endpoint."""
import io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Stores


def make_csv(rows):
    """Helper to create CSV content from rows."""
    headers = [
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
    lines = [",".join(headers)]
    for row in rows:
        lines.append(",".join(str(v) for v in row))
    return "\n".join(lines).encode()


class TestCSVImportSuccess:
    """Test successful CSV imports."""

    def test_import_single_store(self, client: TestClient, admin_token: str, db_session: Session):
        """Test importing a single store."""
        csv_data = make_csv(
            [
                [
                    "S3000",
                    "Import Store",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "100 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3000",
                    "pharmacy|pickup",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_rows_processed"] == 1
        assert data["successfully_created"] == 1
        assert data["successfully_updated"] == 0
        assert data["failed"] == []

    def test_import_multiple_stores(self, client: TestClient, admin_token: str):
        """Test importing multiple stores."""
        csv_data = make_csv(
            [
                [
                    "S3001",
                    "Store 1",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "101 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3001",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ],
                [
                    "S3002",
                    "Store 2",
                    "regular",
                    "active",
                    "42.3650",
                    "-71.0585",
                    "102 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3002",
                    "pickup",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ],
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_rows_processed"] == 2
        assert data["successfully_created"] == 2

    def test_import_upsert_existing_store(self, client: TestClient, admin_token: str, db_session: Session):
        """Test updating existing store during import."""
        # Create initial store
        existing_store = Stores(
            store_id="S3003",
            name="Original Name",
            store_type="regular",
            address_street="103 Cambridge St",
            address_city="Boston",
            address_state="MA",
            address_postal_code="02114",
            address_country="USA",
            latitude=42.3601,
            longitude=-71.0589,
            phone="617-555-3003",
            hours_mon="08:00-22:00",
            hours_tue="08:00-22:00",
            hours_wed="08:00-22:00",
            hours_thu="08:00-22:00",
            hours_fri="08:00-22:00",
            hours_sat="09:00-21:00",
            hours_sun="10:00-20:00",
            status="active",
        )
        db_session.add(existing_store)
        db_session.commit()

        # Import updated version
        csv_data = make_csv(
            [
                [
                    "S3003",
                    "Updated Name",
                    "flagship",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "103 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3003",
                    "pharmacy|pickup",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["successfully_updated"] == 1


class TestCSVImportValidation:
    """Test CSV import validation errors."""

    def test_import_invalid_phone(self, client: TestClient, admin_token: str):
        """Test import with invalid phone format."""
        csv_data = make_csv(
            [
                [
                    "S3004",
                    "Bad Phone Store",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "104 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "bad-phone",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 400
        data = response.json()
        assert len(data["detail"]["failed"]) == 1
        assert "phone" in data["detail"]["failed"][0]["error"].lower()

    def test_import_duplicate_store_id(self, client: TestClient, admin_token: str):
        """Test import with duplicate store_id in same file."""
        csv_data = make_csv(
            [
                [
                    "S3005",
                    "Store A",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "105 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3005",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ],
                [
                    "S3005",
                    "Store B",
                    "regular",
                    "active",
                    "42.3650",
                    "-71.0585",
                    "106 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3006",
                    "pickup",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ],
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 400
        data = response.json()
        assert "duplicate" in data["detail"]["failed"][0]["error"].lower()

    def test_import_invalid_store_type(self, client: TestClient, admin_token: str):
        """Test import with invalid store type."""
        csv_data = make_csv(
            [
                [
                    "S3007",
                    "Bad Type Store",
                    "invalid_type",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "107 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3007",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {admin_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 400


class TestCSVImportPermissions:
    """Test CSV import permissions."""

    def test_import_viewer_denied(self, client: TestClient, viewer_token: str):
        """Test viewer cannot import stores."""
        csv_data = make_csv(
            [
                [
                    "S3008",
                    "Test Store",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "108 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3008",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {viewer_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 403

    def test_import_marketer_allowed(self, client: TestClient, marketer_token: str):
        """Test marketer can import stores."""
        csv_data = make_csv(
            [
                [
                    "S3009",
                    "Marketer Store",
                    "regular",
                    "active",
                    "42.3601",
                    "-71.0589",
                    "109 Cambridge St",
                    "Boston",
                    "MA",
                    "02114",
                    "USA",
                    "617-555-3009",
                    "pharmacy",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "08:00-22:00",
                    "09:00-21:00",
                    "10:00-20:00",
                ]
            ]
        )

        response = client.post(
            "/api/admin/stores/import",
            headers={"Authorization": f"Bearer {marketer_token}"},
            files={"file": ("test.csv", io.BytesIO(csv_data), "text/csv")},
        )

        assert response.status_code == 200
