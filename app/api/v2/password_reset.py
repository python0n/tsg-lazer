"""Admin-initiated password reset (Shiina-style, no email required).

Flow:
  1. Admin calls POST /admin/users/{id}/reset-password -> gets a one-time token.
     Admin sends the link (https://SITE/reset?token=...) to the user manually.
  2. User opens the link (while logged OUT) and sets a new password via
     POST /password-reset/{token}.

Tokens are single-use and expire after RESET_TTL. Verify/confirm are PUBLIC
(no auth) and never return 401/403 (would confuse the BFF auth wrapper) — they
use 404/410/422 instead.
"""

import secrets
from datetime import UTC
from datetime import datetime
from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AdminUser
from app.api.deps import DbSession
from app.core.security import get_password_hash
from app.models.user import PasswordReset
from app.models.user import User
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import status

router = APIRouter(tags=["Password reset"])

RESET_TTL = timedelta(hours=1)


class ResetTokenResponse(BaseModel):
    token: str
    expires_at: datetime


class ResetInfoResponse(BaseModel):
    username: str


class ResetConfirm(BaseModel):
    new_password: str


@router.post("/admin/users/{user_id}/reset-password", response_model=ResetTokenResponse)
async def generate_reset(admin: AdminUser, db: DbSession, user_id: int) -> ResetTokenResponse:
    """Admin generates a one-time reset token for a user."""
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Invalidate any previous unused tokens for this user.
    old = (
        await db.execute(
            select(PasswordReset).where(
                PasswordReset.user_id == user_id, PasswordReset.used == False,  # noqa: E712
            ),
        )
    ).scalars().all()
    for row in old:
        row.used = True

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + RESET_TTL
    db.add(PasswordReset(token=token, user_id=user_id, expires_at=expires_at, used=False))
    await db.commit()
    return ResetTokenResponse(token=token, expires_at=expires_at)


async def _load_valid(db: DbSession, token: str) -> PasswordReset:
    row = (
        await db.execute(select(PasswordReset).where(PasswordReset.token == token))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid reset link")
    if row.used:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This reset link was already used")
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if datetime.now(UTC) > exp:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="This reset link has expired")
    return row


@router.get("/password-reset/{token}", response_model=ResetInfoResponse)
async def verify_reset(db: DbSession, token: str) -> ResetInfoResponse:
    """Public: check a token is valid and return the target username."""
    row = await _load_valid(db, token)
    user = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid reset link")
    return ResetInfoResponse(username=user.username)


@router.post("/password-reset/{token}", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_reset(db: DbSession, token: str, payload: ResetConfirm) -> None:
    """Public: set a new password using a valid token."""
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be at least 8 characters",
        )
    row = await _load_valid(db, token)
    user = (
        await db.execute(select(User).where(User.id == row.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid reset link")

    user.password_hash = get_password_hash(payload.new_password)
    row.used = True
    await db.commit()
