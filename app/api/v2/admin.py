"""Admin panel endpoints (require ADMINISTRATOR privilege).

Mirrors the core of Shiina's /ap user management: list users, view one, and
moderate (restrict/unrestrict, edit role privileges). Password reset by email is
intentionally NOT here yet (needs SMTP + a token table) — separate feature.
"""

from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy import or_
from sqlalchemy import select

from app.api.deps import AdminUser
from app.api.deps import DbSession
from app.core import privileges as priv
from app.core.bbcode import MAX_PAGE_LENGTH
from app.core.bbcode import render_user_page
from app.models.user import User
from fastapi import APIRouter
from fastapi import File
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from fastapi import UploadFile

router = APIRouter(prefix="/admin", tags=["Admin"])


class AdminUserRow(BaseModel):
    id: int
    username: str
    email: str
    country_code: str
    privileges: int
    is_admin: bool
    is_staff: bool
    is_supporter: bool
    is_restricted: bool
    is_bot: bool


class AdminUserList(BaseModel):
    users: list[AdminUserRow]
    total: int
    page: int
    pages: int


class AdminUserUpdate(BaseModel):
    is_restricted: bool | None = None
    privileges: int | None = None


def _row(u: User) -> AdminUserRow:
    return AdminUserRow(
        id=u.id,
        username=u.username,
        email=u.email,
        country_code=u.country_acronym,
        privileges=u.privileges,
        is_admin=priv.is_admin(u.privileges),
        is_staff=priv.is_staff(u.privileges),
        is_supporter=u.is_supporter,
        is_restricted=u.is_restricted,
        is_bot=u.is_bot,
    )


@router.get("/users", response_model=AdminUserList)
async def list_users(
    admin: AdminUser,
    db: DbSession,
    query: str | None = Query(None, max_length=50),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
) -> AdminUserList:
    """Paginated user list with optional username/email search."""
    where = []
    if query:
        q = query.strip()
        if q:
            like = f"%{q}%"
            where.append(or_(User.username.ilike(like), User.email.ilike(like)))

    total = (
        await db.execute(select(func.count()).select_from(User).where(*where))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(User)
                .where(*where)
                .order_by(User.id)
                .offset((page - 1) * limit)
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )

    pages = max(1, (total + limit - 1) // limit)
    return AdminUserList(
        users=[_row(u) for u in rows], total=total, page=page, pages=pages,
    )


@router.get("/users/{user_id}", response_model=AdminUserRow)
async def get_user_admin(admin: AdminUser, db: DbSession, user_id: int) -> AdminUserRow:
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _row(user)


@router.patch("/users/{user_id}", response_model=AdminUserRow)
async def update_user_admin(
    admin: AdminUser,
    db: DbSession,
    user_id: int,
    payload: AdminUserUpdate,
) -> AdminUserRow:
    """Restrict/unrestrict and/or set privilege bitflags on a user."""
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.is_bot:
        # Bot accounts are managed by the server, not through the admin panel.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bot accounts cannot be modified",
        )

    if payload.is_restricted is not None:
        # Guard against self-lockout.
        if user.id == admin.id and payload.is_restricted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot restrict your own account",
            )
        user.is_restricted = payload.is_restricted

    if payload.privileges is not None:
        if payload.privileges < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid privileges value",
            )
        # Guard against removing your own admin bit (self-lockout from panel).
        if user.id == admin.id and not priv.is_admin(payload.privileges):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot remove your own administrator privilege",
            )
        user.privileges = payload.privileges

    await db.commit()
    await db.refresh(user)
    return _row(user)


class UserPageUpdate(BaseModel):
    content: str


@router.put("/users/{user_id}/page", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_page(
    admin: AdminUser,
    db: DbSession,
    user_id: int,
    payload: UserPageUpdate,
) -> None:
    """Set a user's "me!" page (raw BBCode, rendered server-side).

    Intentionally allowed for bot accounts — bots cannot log in, so an admin
    editing their page from the panel/profile is the only way to set it.
    """
    if len(payload.content) > MAX_PAGE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Page content too long (max {MAX_PAGE_LENGTH} characters)",
        )

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    raw = payload.content.strip()
    user.user_page = raw or None
    user.user_page_html = render_user_page(raw) if raw else None
    await db.commit()


@router.post("/users/{user_id}/cover", status_code=status.HTTP_204_NO_CONTENT)
async def set_user_cover(
    admin: AdminUser,
    db: DbSession,
    user_id: int,
    cover: UploadFile = File(...),
) -> None:
    """Upload/replace a user's cover/banner as an admin.

    Intentionally allowed for bot accounts — bots cannot log in, so this is
    the only way to give them a banner.
    """
    from app.api.v2.me import _COVER_TYPES
    from app.api.v2.me import _public_url
    from app.api.v2.me import _store_image
    from app.core.config import settings as app_settings

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    filename = await _store_image(
        cover,
        directory_path=app_settings.covers_path,
        user_id=user.id,
        allowed=_COVER_TYPES,
        max_mb=app_settings.cover_max_mb,
        fit=(2400, 1000),
    )
    user.cover_url = _public_url("covers", filename)
    await db.commit()
