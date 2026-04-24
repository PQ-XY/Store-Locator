import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.database import SessionLocal
from app.models import StoreService, Stores, StoreStatus, StoreType

EXPECTED_HEADERS = [
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

HOUR_COLUMNS = [
    "hours_mon",
    "hours_tue",
    "hours_wed",
    "hours_thu",
    "hours_fri",
    "hours_sat",
    "hours_sun",
]

ALLOWED_SERVICES = {
    "pharmacy",
    "pickup",
    "returns",
    "optical",
    "photo_printing",
    "gift_wrapping",
    "automotive",
    "garden_center",
}

PHONE_PATTERN = re.compile(r"^\d{3}-\d{3}-\d{4}$")
HOURS_PATTERN = re.compile(r"^([01]\d|2[0-4]):([0-5]\d)-([01]\d|2[0-4]):([0-5]\d)$")


@dataclass
class ParsedStoreRow:
    store_id: str
    name: str
    store_type: StoreType
    status: StoreStatus
    latitude: float
    longitude: float
    address_street: str
    address_city: str
    address_state: str
    address_postal_code: str
    address_country: str
    phone: str
    services: list[str]
    hours_mon: str
    hours_tue: str
    hours_wed: str
    hours_thu: str
    hours_fri: str
    hours_sat: str
    hours_sun: str


def _time_to_minutes(value: str) -> int:
    hour_str, minute_str = value.split(":")
    hour = int(hour_str)
    minute = int(minute_str)
    if hour == 24 and minute != 0:
        raise ValueError("24 is only valid with :00")
    return hour * 60 + minute


def validate_hours(value: str) -> None:
    if value == "closed":
        return

    match = HOURS_PATTERN.match(value)
    if not match:
        raise ValueError("must be HH:MM-HH:MM or closed")

    open_time = _time_to_minutes(f"{match.group(1)}:{match.group(2)}")
    close_time = _time_to_minutes(f"{match.group(3)}:{match.group(4)}")
    if open_time >= close_time:
        raise ValueError("open time must be earlier than close time")


def validate_services(value: str) -> list[str]:
    if " " in value:
        raise ValueError("must not contain spaces")
    parts = [svc for svc in value.split("|") if svc]
    if not parts:
        raise ValueError("must contain at least one service")

    invalid = [svc for svc in parts if svc not in ALLOWED_SERVICES]
    if invalid:
        raise ValueError(f"contains unsupported service(s): {', '.join(invalid)}")

    if len(parts) != len(set(parts)):
        raise ValueError("contains duplicate service values")

    return parts


def parse_row(row_number: int, row: dict[str, str]) -> ParsedStoreRow:
    missing_fields = [key for key in EXPECTED_HEADERS if not row.get(key)]
    if missing_fields:
        raise ValueError(f"missing required field(s): {', '.join(missing_fields)}")

    try:
        store_type = StoreType(row["store_type"])
    except ValueError as exc:
        raise ValueError("invalid store_type") from exc

    try:
        status = StoreStatus(row["status"])
    except ValueError as exc:
        raise ValueError("invalid status") from exc

    try:
        latitude = float(row["latitude"])
        longitude = float(row["longitude"])
    except ValueError as exc:
        raise ValueError("latitude/longitude must be numeric") from exc

    if latitude < -90 or latitude > 90:
        raise ValueError("latitude out of range")
    if longitude < -180 or longitude > 180:
        raise ValueError("longitude out of range")

    if len(row["address_state"]) != 2:
        raise ValueError("address_state must be 2 letters")

    if not row["address_postal_code"].isdigit() or len(row["address_postal_code"]) != 5:
        raise ValueError("address_postal_code must be 5 digits")

    if not PHONE_PATTERN.match(row["phone"]):
        raise ValueError("phone must match XXX-XXX-XXXX")

    services = validate_services(row["services"])

    for hours_column in HOUR_COLUMNS:
        try:
            validate_hours(row[hours_column])
        except ValueError as exc:
            raise ValueError(f"{hours_column} {exc}") from exc

    return ParsedStoreRow(
        store_id=row["store_id"],
        name=row["name"],
        store_type=store_type,
        status=status,
        latitude=latitude,
        longitude=longitude,
        address_street=row["address_street"],
        address_city=row["address_city"],
        address_state=row["address_state"],
        address_postal_code=row["address_postal_code"],
        address_country=row["address_country"],
        phone=row["phone"],
        services=services,
        hours_mon=row["hours_mon"],
        hours_tue=row["hours_tue"],
        hours_wed=row["hours_wed"],
        hours_thu=row["hours_thu"],
        hours_fri=row["hours_fri"],
        hours_sat=row["hours_sat"],
        hours_sun=row["hours_sun"],
    )


def load_csv_rows(csv_path: Path) -> tuple[list[ParsedStoreRow], list[str]]:
    parsed_rows: list[ParsedStoreRow] = []
    errors: list[str] = []

    with csv_path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames != EXPECTED_HEADERS:
            return [], [
                "CSV headers do not match expected format exactly. "
                f"Expected: {','.join(EXPECTED_HEADERS)}"
            ]

        for index, row in enumerate(reader, start=2):
            try:
                parsed_rows.append(parse_row(index, row))
            except ValueError as exc:
                errors.append(f"row {index}: {exc}")

    return parsed_rows, errors


def upsert_rows(rows: list[ParsedStoreRow]) -> tuple[int, int]:
    created = 0
    updated = 0
    session = SessionLocal()

    try:
        with session.begin():
            existing_ids = {
                store_id
                for (store_id,) in session.execute(
                    select(Stores.store_id).where(Stores.store_id.in_([r.store_id for r in rows]))
                )
            }

            for row in rows:
                if row.store_id in existing_ids:
                    store = session.get(Stores, row.store_id)
                    updated += 1
                else:
                    store = Stores(store_id=row.store_id)
                    session.add(store)
                    created += 1

                store.name = row.name
                store.store_type = row.store_type
                store.status = row.status
                store.latitude = row.latitude
                store.longitude = row.longitude
                store.address_street = row.address_street
                store.address_city = row.address_city
                store.address_state = row.address_state
                store.address_postal_code = row.address_postal_code
                store.address_country = row.address_country
                store.phone = row.phone
                store.hours_mon = row.hours_mon
                store.hours_tue = row.hours_tue
                store.hours_wed = row.hours_wed
                store.hours_thu = row.hours_thu
                store.hours_fri = row.hours_fri
                store.hours_sat = row.hours_sat
                store.hours_sun = row.hours_sun

                store.services.clear()
                for service_name in row.services:
                    store.services.append(StoreService(service_name=service_name))

        return created, updated
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Import stores CSV into PostgreSQL")
    parser.add_argument("csv_file", type=str, help="Path to stores CSV file")
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}")
        return 1

    rows, errors = load_csv_rows(csv_path)
    if errors:
        print("Import failed due to validation errors:")
        for err in errors[:20]:
            print(f"- {err}")
        if len(errors) > 20:
            print(f"... and {len(errors) - 20} more errors")
        print(
            "Report: "
            f"total_rows={len(rows) + len(errors)}, created=0, updated=0, failed={len(errors)}"
        )
        return 1

    created, updated = upsert_rows(rows)
    print(
        "Report: "
        f"total_rows={len(rows)}, created={created}, updated={updated}, failed=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())