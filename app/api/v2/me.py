"""Current user (/me) endpoints."""

import io
import re
import time
from pathlib import Path as FsPath

from fastapi import APIRouter
from fastapi import File
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status
from PIL import Image
from PIL import ImageOps
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy import select

from app.api.deps import CurrentUser
from app.api.deps import DbSession
from app.api.v2.schemas import RankHistoryResponse
from app.api.v2.schemas import UserResponse
from app.api.v2.schemas import UserStatisticsResponse
from app.core import privileges
from app.core.config import settings
from app.core.security import get_password_hash
from app.core.security import verify_password
from app.models.user import GameMode
from app.models.user import User

router = APIRouter()


def _mode_to_string(mode: GameMode) -> str:
    """Convert GameMode enum to string."""
    return {
        GameMode.OSU: "osu",
        GameMode.TAIKO: "taiko",
        GameMode.CATCH: "fruits",
        GameMode.MANIA: "mania",
    }.get(mode, "osu")


def _get_user_statistics(user: CurrentUser, mode: GameMode) -> UserStatisticsResponse:
    """Get user statistics for a specific mode."""
    mode_str = _mode_to_string(mode)

    for stats in user.statistics:
        if stats.mode == mode:
            # User is ranked if they have a global rank
            is_ranked = stats.global_rank is not None

            # Only provide rank_history if user is ranked
            rank_history = None
            if is_ranked:
                rank_history = RankHistoryResponse(mode=mode_str, data=[])

            return UserStatisticsResponse(
                ranked_score=stats.ranked_score,
                total_score=stats.total_score,
                pp=stats.pp,
                global_rank=stats.global_rank,
                global_rank_percent=None,
                country_rank=stats.country_rank,
                is_ranked=is_ranked,
                rank_history=rank_history,
                hit_accuracy=stats.hit_accuracy,
                play_count=stats.play_count,
                play_time=stats.play_time,
                total_hits=stats.total_hits,
                maximum_combo=stats.maximum_combo,
                replays_watched=stats.replays_watched,
                grade_counts={
                    "ss": stats.grade_ss,
                    "ssh": stats.grade_ssh,
                    "s": stats.grade_s,
                    "sh": stats.grade_sh,
                    "a": stats.grade_a,
                },
                level={
                    "current": stats.level,
                    "progress": stats.level_progress,
                },
            )

    # Return unranked stats if none found
    return UserStatisticsResponse(
        is_ranked=False,
        rank_history=None,
    )


def _all_statistics(user: CurrentUser) -> dict:
    """Build the per-ruleset statistics dict the client panel reads."""
    modes = {
        "osu": GameMode.OSU,
        "taiko": GameMode.TAIKO,
        "fruits": GameMode.CATCH,
        "mania": GameMode.MANIA,
    }
    return {name: _get_user_statistics(user, m) for name, m in modes.items()}


@router.get("/me", response_model=UserResponse)
@router.get("/me/", response_model=UserResponse, include_in_schema=False)
async def get_current_user(user: CurrentUser) -> UserResponse:
    """Get the current authenticated user's profile."""
    mode = user.playmode
    stats = _get_user_statistics(user, mode)

    return UserResponse(
        id=user.id,
        username=user.username,
        avatar_url=user.avatar_url,
        cover_url=user.cover_url,
        country_code=user.country_acronym,
        title=user.title,
        playmode=_mode_to_string(mode),
        playstyle=user.playstyle.split(",") if user.playstyle else None,
        is_active=user.is_active,
        is_bot=user.is_bot,
        is_admin=privileges.is_admin(user.privileges),
        is_staff=privileges.is_staff(user.privileges),
        is_supporter=user.is_supporter,
        is_restricted=user.is_restricted,
        join_date=user.created_at,
        last_visit=user.last_visit,
        statistics=stats,
        statistics_rulesets=_all_statistics(user),
    )


@router.get("/me/{mode}", response_model=UserResponse)
async def get_current_user_mode(user: CurrentUser, mode: str) -> UserResponse:
    """Get the current authenticated user's profile for a specific mode."""
    mode_enum = {
        "osu": GameMode.OSU,
        "taiko": GameMode.TAIKO,
        "fruits": GameMode.CATCH,
        "mania": GameMode.MANIA,
    }.get(mode, GameMode.OSU)

    stats = _get_user_statistics(user, mode_enum)

    return UserResponse(
        id=user.id,
        username=user.username,
        avatar_url=user.avatar_url,
        cover_url=user.cover_url,
        country_code=user.country_acronym,
        title=user.title,
        playmode=mode,
        playstyle=user.playstyle.split(",") if user.playstyle else None,
        is_active=user.is_active,
        is_bot=user.is_bot,
        is_admin=privileges.is_admin(user.privileges),
        is_staff=privileges.is_staff(user.privileges),
        is_supporter=user.is_supporter,
        is_restricted=user.is_restricted,
        join_date=user.created_at,
        last_visit=user.last_visit,
        statistics=stats,
        statistics_rulesets=_all_statistics(user),
    )


# ---------------------------------------------------------------------------
# Profile customization (avatar / cover / username / country)
#
# No supporter gating: any user may change these. Files are written under
# settings.avatars_path / settings.covers_path as "{user_id}.{ext}" and served
# publicly at settings.assets_base_url (the operator points their asset host /
# reverse proxy at those folders). avatar_url / cover_url are stored on the user
# row with a cache-busting ?v= so clients pick up changes immediately.
# ---------------------------------------------------------------------------

# osu-web style: 2-15 chars, letters/digits/_/[/]/-/space, no leading/trailing space.
_USERNAME_RE = re.compile(r"^(?! )[\w\[\] -]{2,15}(?<! )$")

# ISO 3166-1 alpha-2 country codes (uppercase) for flag validation.
_ISO_COUNTRIES = frozenset(
    """AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI
    BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO
    CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO
    FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT
    HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY
    KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP
    MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE
    PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH
    SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
    TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM
    ZW""".split()
)

# content-type -> stored extension. JPEG is re-encoded to PNG.
_AVATAR_TYPES = {"image/png": "png", "image/jpeg": "png", "image/gif": "gif"}
_COVER_TYPES = {"image/png": "png", "image/jpeg": "png"}


def _public_url(kind: str, filename: str) -> str:
    """Build the cache-busted public URL for a stored asset."""
    base = settings.assets_base_url.rstrip("/")
    return f"{base}/{kind}/{filename}?v={int(time.time())}"


def _clear_existing(directory: FsPath, user_id: int) -> None:
    """Delete any previously stored file for this user (png/jpg/jpeg/gif)."""
    for old in directory.glob(f"{user_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass


async def _store_image(
    upload: UploadFile,
    *,
    directory_path: str,
    user_id: int,
    allowed: dict[str, str],
    max_mb: int,
    fit: tuple[int, int] | None,
    square: bool = False,
) -> str:
    """Validate, (optionally) resize and persist an uploaded image.

    Returns the stored filename ("{id}.{ext}"). Raises HTTPException on bad input.
    """
    ext = allowed.get((upload.content_type or "").lower())
    if ext is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type (PNG, JPEG"
            + (" or GIF" if "image/gif" in allowed else "") + " only)",
        )

    raw = await upload.read()
    if len(raw) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {max_mb} MB)",
        )

    directory = FsPath(directory_path)
    directory.mkdir(parents=True, exist_ok=True)
    _clear_existing(directory, user_id)
    dest = directory / f"{user_id}.{ext}"

    if ext == "gif":
        dest.write_bytes(raw)
    else:
        try:
            img = Image.open(io.BytesIO(raw))
            img = img.convert("RGB")
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or corrupt image",
            )
        if square and fit is not None:
            # Exact square crop-to-center (osu-web style avatars).
            img = ImageOps.fit(
                img, fit, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
            )
        elif fit is not None:
            img.thumbnail(fit)  # preserves aspect ratio, never upscales
        img.save(dest, format="PNG")

    return f"{user_id}.{ext}"


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    user: CurrentUser,
    db: DbSession,
    avatar: UploadFile = File(...),
) -> UserResponse:
    """Upload/replace the current user's avatar (PNG/JPEG/GIF, square-ish)."""
    filename = await _store_image(
        avatar,
        directory_path=settings.avatars_path,
        user_id=user.id,
        allowed=_AVATAR_TYPES,
        max_mb=settings.avatar_max_mb,
        fit=(500, 500),
        square=True,
    )
    user.avatar_url = _public_url("avatars", filename)
    await db.commit()
    await db.refresh(user)
    return await get_current_user(user)


@router.post("/me/cover", response_model=UserResponse)
async def upload_cover(
    user: CurrentUser,
    db: DbSession,
    cover: UploadFile = File(...),
) -> UserResponse:
    """Upload/replace the current user's profile cover/banner (PNG/JPEG)."""
    filename = await _store_image(
        cover,
        directory_path=settings.covers_path,
        user_id=user.id,
        allowed=_COVER_TYPES,
        max_mb=settings.cover_max_mb,
        fit=(2400, 1000),
    )
    user.cover_url = _public_url("covers", filename)
    await db.commit()
    await db.refresh(user)
    return await get_current_user(user)


class ProfileUpdate(BaseModel):
    """Editable profile fields. All optional; only provided ones change."""

    username: str | None = None
    country: str | None = None  # ISO 3166-1 alpha-2


@router.patch("/me", response_model=UserResponse)
async def update_profile(
    user: CurrentUser,
    db: DbSession,
    payload: ProfileUpdate,
) -> UserResponse:
    """Update the current user's username and/or country flag."""
    changed = False

    if payload.username is not None:
        name = payload.username.strip()
        if not _USERNAME_RE.match(name):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid username (2-15 chars: letters, digits, _ [ ] - space)",
            )
        if name.lower() != user.username.lower():
            taken = (
                await db.execute(
                    select(User.id).where(
                        func.lower(User.username) == name.lower(),
                        User.id != user.id,
                    ),
                )
            ).first()
            if taken:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Username already taken",
                )
        user.username = name
        changed = True

    if payload.country is not None:
        cc = payload.country.strip().upper()
        if cc not in _ISO_COUNTRIES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid country code",
            )
        user.country_acronym = cc
        changed = True

    if changed:
        await db.commit()
        await db.refresh(user)

    return await get_current_user(user)


class PasswordChange(BaseModel):
    """Self-service password change (requires the current password)."""

    current_password: str
    new_password: str


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    user: CurrentUser,
    db: DbSession,
    payload: PasswordChange,
) -> None:
    """Change the logged-in user's password.

    Returns 422 (NOT 401/403) on a wrong current password, so the BFF's auth
    wrapper doesn't mistake it for an expired token and trigger a refresh/logout.
    """
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Current password is incorrect",
        )
    if len(payload.new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must be at least 8 characters",
        )
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must differ from the current one",
        )
    user.password_hash = get_password_hash(payload.new_password)
    await db.commit()


# ---------------------------------------------------------------------------
# User page ("me!" section) & profile section order
# ---------------------------------------------------------------------------

from app.api.v2.schemas import PROFILE_SECTIONS  # noqa: E402
from app.core.bbcode import MAX_PAGE_LENGTH  # noqa: E402
from app.core.bbcode import render_user_page  # noqa: E402


class UserPageBody(BaseModel):
    content: str


@router.put("/me/page")
async def set_my_page(user: CurrentUser, db: DbSession, payload: UserPageBody) -> dict:
    """Set the current user's "me!" page (raw BBCode, rendered server-side)."""
    if len(payload.content) > MAX_PAGE_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Page content too long (max {MAX_PAGE_LENGTH} characters)",
        )
    raw = payload.content.strip()
    user.user_page = raw or None
    user.user_page_html = render_user_page(raw) if raw else None
    await db.commit()
    return {"raw": user.user_page or "", "html": user.user_page_html or ""}


class ProfileOrderBody(BaseModel):
    order: list[str]


@router.put("/me/profile-order", status_code=status.HTTP_204_NO_CONTENT)
async def set_my_profile_order(
    user: CurrentUser, db: DbSession, payload: ProfileOrderBody,
) -> None:
    """Set profile section order (osu-web extras_order style).

    The order must be a permutation of the known sections.
    """
    if sorted(payload.order) != sorted(PROFILE_SECTIONS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Order must contain exactly: {', '.join(PROFILE_SECTIONS)}",
        )
    user.profile_order = ",".join(payload.order)
    await db.commit()
