import hashlib
import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import DBSession
from app.models import Permission, PermissionStatus, RefreshToken, RolePermission, RoleStatus, User, UserStatus

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me-32-plus-chars")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

router = APIRouter(prefix="/api/auth", tags=["auth"])
http_bearer = HTTPBearer(auto_error=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_token_expires_in: int
    refresh_token_expires_in: int
    must_change_password: bool


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=10)


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    access_token_expires_in: int


class LogoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=10)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_token(user: User, token_type: str, expires_delta: timedelta) -> tuple[str, datetime]:
    expires_at = _utcnow() + expires_delta
    payload = {
        "user_id": user.user_id,
        "email": user.email,
        "role": user.role.name,
        "type": token_type,
        "exp": expires_at,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, expires_at


def _decode_token(token: str, expected_type: str, verify_exp: bool = True) -> dict:
    decode_options = {
        "require": ["exp", "user_id", "email", "role", "type"],
        "verify_exp": verify_exp,
    }
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            options=decode_options,
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Invalid token type")

    return payload


def _authenticate_user(db: DBSession, email: str, password: str) -> User | None:
    user = db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(User.email == email)
    ).scalar_one_or_none()

    if user is None:
        return None
    if user.status != UserStatus.ACTIVE:
        return None
    if user.role.status != RoleStatus.ACTIVE:
        return None
    if not bcrypt.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8")):
        return None
    return user


def get_current_user(
    db: DBSession,
    credentials: HTTPAuthorizationCredentials | None = Depends(http_bearer),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing bearer token")

    payload = _decode_token(credentials.credentials, expected_type="access")

    user = db.execute(
        select(User)
        .options(selectinload(User.role))
        .where(
            User.user_id == payload["user_id"],
            User.email == payload["email"],
            User.status == UserStatus.ACTIVE,
        )
    ).scalar_one_or_none()

    if user is None or user.role.status != RoleStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="User is not active")

    return user


def require_roles(*allowed_roles: str):
    allowed = {role.lower() for role in allowed_roles}

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.name.lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return dependency


def _active_permission_codes_for_user(db: DBSession, user: User) -> set[str]:
    rows = db.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(
            RolePermission.role_id == user.role_id,
            Permission.status == PermissionStatus.ACTIVE,
        )
    ).all()
    return {row[0] for row in rows}


def require_permissions(*required_permissions: str):
    required = set(required_permissions)

    def dependency(db: DBSession, current_user: User = Depends(get_current_user)) -> User:
        granted = _active_permission_codes_for_user(db, current_user)
        if not required.issubset(granted):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource",
            )
        return current_user

    return dependency


@router.post("/login", response_model=TokenPairResponse)
def login(payload: LoginRequest, db: DBSession) -> TokenPairResponse:
    user = _authenticate_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access_token, _ = _create_token(
        user,
        token_type="access",
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    refresh_token, refresh_expires_at = _create_token(
        user,
        token_type="refresh",
        expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )

    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh_token),
            expires_at=refresh_expires_at,
        )
    )
    db.commit()

    return TokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token_expires_in=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        must_change_password=user.must_change_password,
    )


@router.post("/refresh", response_model=RefreshResponse)
def refresh_access_token(payload: RefreshRequest, db: DBSession) -> RefreshResponse:
    token_payload = _decode_token(payload.refresh_token, expected_type="refresh")
    token_hash = _hash_token(payload.refresh_token)

    token_record = db.execute(
        select(RefreshToken)
        .options(selectinload(RefreshToken.user).selectinload(User.role))
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > _utcnow(),
        )
    ).scalar_one_or_none()

    if token_record is None:
        raise HTTPException(status_code=401, detail="Refresh token is invalid or revoked")

    user = token_record.user
    if user.status != UserStatus.ACTIVE or user.role.status != RoleStatus.ACTIVE:
        raise HTTPException(status_code=401, detail="User is not active")

    if user.user_id != token_payload["user_id"] or user.email != token_payload["email"]:
        raise HTTPException(status_code=401, detail="Refresh token does not match user")

    access_token, _ = _create_token(
        user,
        token_type="access",
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return RefreshResponse(
        access_token=access_token,
        access_token_expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/logout")
def logout(payload: LogoutRequest, db: DBSession) -> dict[str, str]:
    # Verify signature/type but allow expired refresh tokens to be revoked as well.
    _decode_token(payload.refresh_token, expected_type="refresh", verify_exp=False)
    token_hash = _hash_token(payload.refresh_token)

    token_record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if token_record is not None and token_record.revoked_at is None:
        token_record.revoked_at = _utcnow()
        db.commit()

    return {"status": "logged_out"}
