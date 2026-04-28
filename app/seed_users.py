import argparse
from dataclasses import dataclass

import bcrypt
from sqlalchemy import select

from app.database import SessionLocal, create_db_and_tables
from app.models import (
    Permission,
    PermissionStatus,
    Role,
    RolePermission,
    RoleStatus,
    User,
    UserStatus,
)

DEFAULT_PASSWORD = "TestPassword123!"

ROLE_DEFINITIONS = [
    {
        "name": "admin",
        "description": "Full access to all endpoints",
    },
    {
        "name": "marketer",
        "description": "Store management and batch imports",
    },
    {
        "name": "viewer",
        "description": "Read-only access to stores",
    },
]

PERMISSION_DEFINITIONS = [
    {
        "code": "stores.read",
        "description": "Read store data",
    },
    {
        "code": "stores.write",
        "description": "Create, update, and deactivate stores",
    },
    {
        "code": "stores.import",
        "description": "Perform batch CSV imports",
    },
    {
        "code": "users.manage",
        "description": "Create, update, and deactivate users",
    },
]

ROLE_PERMISSION_MAP = {
    "admin": {"stores.read", "stores.write", "stores.import", "users.manage"},
    "marketer": {"stores.read", "stores.write", "stores.import"},
    "viewer": {"stores.read"},
}

USER_DEFINITIONS = [
    {
        "user_id": "U001",
        "email": "admin@test.com",
        "role_name": "admin",
    },
    {
        "user_id": "U002",
        "email": "marketer@test.com",
        "role_name": "marketer",
    },
    {
        "user_id": "U003",
        "email": "viewer@test.com",
        "role_name": "viewer",
    },
]


@dataclass
class SeedResult:
    created_roles: int = 0
    updated_roles: int = 0
    created_permissions: int = 0
    updated_permissions: int = 0
    created_role_permissions: int = 0
    created_users: int = 0
    updated_users: int = 0


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_roles(session) -> dict[str, Role]:
    roles_by_name: dict[str, Role] = {}
    for role_definition in ROLE_DEFINITIONS:
        role = session.execute(
            select(Role).where(Role.name == role_definition["name"])
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                name=role_definition["name"],
                description=role_definition["description"],
                status=RoleStatus.ACTIVE,
            )
            session.add(role)
        else:
            role.description = role_definition["description"]
            role.status = RoleStatus.ACTIVE
        roles_by_name[role.name] = role
    session.flush()
    return roles_by_name


def seed_permissions(session) -> dict[str, Permission]:
    permissions_by_code: dict[str, Permission] = {}
    for permission_definition in PERMISSION_DEFINITIONS:
        permission = session.execute(
            select(Permission).where(Permission.code == permission_definition["code"])
        ).scalar_one_or_none()
        if permission is None:
            permission = Permission(
                code=permission_definition["code"],
                description=permission_definition["description"],
                status=PermissionStatus.ACTIVE,
            )
            session.add(permission)
        else:
            permission.description = permission_definition["description"]
            permission.status = PermissionStatus.ACTIVE

        permissions_by_code[permission.code] = permission

    session.flush()
    return permissions_by_code


def seed_role_permissions(
    session,
    roles_by_name: dict[str, Role],
    permissions_by_code: dict[str, Permission],
) -> None:
    for role_name, permission_codes in ROLE_PERMISSION_MAP.items():
        role = roles_by_name[role_name]
        existing_rows = session.execute(
            select(RolePermission).where(RolePermission.role_id == role.id)
        ).scalars().all()
        existing_permission_ids = {row.permission_id for row in existing_rows}

        desired_permission_ids = {permissions_by_code[code].id for code in permission_codes}

        for existing_row in existing_rows:
            if existing_row.permission_id not in desired_permission_ids:
                session.delete(existing_row)

        for permission_id in desired_permission_ids:
            if permission_id not in existing_permission_ids:
                session.add(RolePermission(role_id=role.id, permission_id=permission_id))


def seed_users(session, roles_by_name: dict[str, Role]) -> SeedResult:
    result = SeedResult()
    password_hash = hash_password(DEFAULT_PASSWORD)

    for user_definition in USER_DEFINITIONS:
        role = roles_by_name[user_definition["role_name"]]
        user = session.execute(
            select(User).where(User.email == user_definition["email"])
        ).scalar_one_or_none()

        if user is None:
            user = User(
                user_id=user_definition["user_id"],
                email=user_definition["email"],
                password_hash=password_hash,
                role_id=role.id,
                status=UserStatus.ACTIVE,
                must_change_password=True,
            )
            session.add(user)
            result.created_users += 1
        else:
            user.user_id = user_definition["user_id"]
            user.password_hash = password_hash
            user.role_id = role.id
            user.status = UserStatus.ACTIVE
            user.must_change_password = True
            result.updated_users += 1

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed default roles and users")
    parser.parse_args()

    create_db_and_tables()

    session = SessionLocal()
    try:
        with session.begin():
            roles_by_name = seed_roles(session)
            permissions_by_code = seed_permissions(session)
            seed_role_permissions(session, roles_by_name, permissions_by_code)
            result = seed_users(session, roles_by_name)
        print(
            "Seed completed: "
            f"roles={len(roles_by_name)}, permissions={len(permissions_by_code)}, "
            f"created_users={result.created_users}, updated_users={result.updated_users}"
        )
        print(f"Default password for all seed users: {DEFAULT_PASSWORD}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
