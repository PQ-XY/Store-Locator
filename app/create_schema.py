from app.database import Base, engine

# Ensure models are imported so SQLAlchemy metadata is populated.
from app import models  # noqa: F401


def create_schema() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    create_schema()
    print("Schema created successfully")