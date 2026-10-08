"""
Resolve which Orbit user a copilot request acts for.

Order:
1. A valid Orbit JWT (``sub`` = Orbit users.id, signed with ORBIT_JWT_SECRET).
2. If ALLOW_LOCAL_USER is on (single-user local mode): the user whose email is
   LOCAL_USER_EMAIL if set, else the oldest Orbit user. This replaces the
   hardcoded-email lookup that used to sit in Orbit's chat router.
3. Otherwise no user, and the caller rejects the request.
"""
from __future__ import annotations

from typing import Optional

from jose import JWTError, jwt
from sqlalchemy import select

from .config import get_settings
from .db import orbit_session
from .models.orbit import User


class NoUserError(Exception):
    """Raised when no Orbit user can be resolved for a request."""


def user_id_from_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.orbit_jwt_secret, algorithms=[settings.orbit_jwt_algorithm])
    except JWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


async def resolve_user_id(token: Optional[str]) -> str:
    settings = get_settings()
    claimed = user_id_from_token(token)

    async with orbit_session() as db:
        if claimed:
            result = await db.execute(select(User.id).where(User.id == claimed))
            found = result.scalar_one_or_none()
            if found:
                return found

        if settings.allow_local_user:
            if settings.local_user_email:
                result = await db.execute(select(User.id).where(User.email == settings.local_user_email))
                found = result.scalar_one_or_none()
                if found:
                    return found
            result = await db.execute(select(User.id).order_by(User.created_at).limit(1))
            found = result.scalar_one_or_none()
            if found:
                return found

    raise NoUserError(
        "No Orbit user found. Sign in to Orbit (or open it once so it creates the local user), "
        "and check ORBIT_DATABASE_URL points at Orbit's database."
    )
