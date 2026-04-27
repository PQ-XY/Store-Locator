import argparse
from dataclasses import dataclass

import bcrypt
from sqlalchemy import select

from app.database import SessionLocal, create_db_and_tables
from app.models import Role, RoleStatus, User, UserStatus

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
            result = seed_users(session, roles_by_name)
        print(
            "Seed completed: "
            f"roles={len(roles_by_name)}, "
            f"created_users={result.created_users}, updated_users={result.updated_users}"
        )
        print(f"Default password for all seed users: {DEFAULT_PASSWORD}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
