import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class StoreType(str, enum.Enum):
    FLAGSHIP = "flagship"
    REGULAR = "regular"
    OUTLET = "outlet"
    EXPRESS = "express"


class StoreStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    TEMPORARILY_CLOSED = "temporarily_closed"


class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class RoleStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class PermissionStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Stores(Base):
    __tablename__ = "stores"

    store_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    store_type: Mapped[StoreType] = mapped_column(
        Enum(StoreType, name="store_type_enum"), nullable=False
    )
    status: Mapped[StoreStatus] = mapped_column(
        Enum(StoreStatus, name="store_status_enum"), nullable=False, default=StoreStatus.ACTIVE
    )
    latitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    address_street: Mapped[str] = mapped_column(String(255), nullable=False)
    address_city: Mapped[str] = mapped_column(String(120), nullable=False)
    address_state: Mapped[str] = mapped_column(String(2), nullable=False)
    address_postal_code: Mapped[str] = mapped_column(String(5), nullable=False)
    address_country: Mapped[str] = mapped_column(String(3), nullable=False, default="USA")
    phone: Mapped[str] = mapped_column(String(12), nullable=False)
    hours_mon: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_tue: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_wed: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_thu: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_fri: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_sat: Mapped[str] = mapped_column(String(32), nullable=False)
    hours_sun: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    services: Mapped[list["StoreService"]] = relationship(
        back_populates="store", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_stores_latitude_range"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_stores_longitude_range"),
        Index("ix_stores_latitude_longitude", "latitude", "longitude"),
        Index(
            "ix_stores_status_active",
            "status",
            postgresql_where=(status == StoreStatus.ACTIVE),
        ),
        Index("ix_stores_store_type", "store_type"),
        Index("ix_stores_address_postal_code", "address_postal_code"),
    )


class StoreService(Base):
    __tablename__ = "store_services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("stores.store_id", ondelete="CASCADE"), nullable=False
    )
    service_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    store: Mapped[Stores] = relationship(back_populates="services")

    __table_args__ = (
        UniqueConstraint("store_id", "service_name", name="uq_store_services_store_id_service_name"),
        Index("ix_store_services_store_id", "store_id"),
        Index("ix_store_services_service_name", "service_name"),
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[RoleStatus] = mapped_column(
        Enum(RoleStatus, name="role_status_enum"), nullable=False, default=RoleStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    users: Mapped[list["User"]] = relationship(back_populates="role")
    permissions: Mapped[list["RolePermission"]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PermissionStatus] = mapped_column(
        Enum(PermissionStatus, name="permission_status_enum"),
        nullable=False,
        default=PermissionStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    roles: Mapped[list["RolePermission"]] = relationship(
        back_populates="permission", cascade="all, delete-orphan"
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    role: Mapped[Role] = relationship(back_populates="permissions")
    permission: Mapped[Permission] = relationship(back_populates="roles")

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_id_permission_id"),
        Index("ix_role_permissions_role_id", "role_id"),
        Index("ix_role_permissions_permission_id", "permission_id"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status_enum"), nullable=False, default=UserStatus.ACTIVE
    )
    must_change_password: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    role: Mapped[Role] = relationship(back_populates="users")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_users_email", "email"),)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_tokens_token_hash", "token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
    )