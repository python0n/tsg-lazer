"""User endpoints."""

from datetime import date
from datetime import timedelta

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import select

from app.core import privileges
from app.api.deps import DbSession
from app.api.v2.schemas import PROFILE_SECTIONS
from app.api.v2.schemas import RankHistoryResponse
from app.api.v2.schemas import UserCompact
from app.api.v2.schemas import UserResponse
from app.api.v2.schemas import UserStatisticsResponse
from app.models.score import Score
from app.models.user import GameMode
from app.models.user import UserRankHistory
from app.models.user import UserRelation
from app.models.user import User


async def _follower_count(db, user_id: int) -> int:
    """Number of users who have friended this user (followers)."""
    return (
        await db.execute(
            select(func.count())
            .select_from(UserRelation)
            .where(
                and_(
                    UserRelation.target_id == user_id,
                    UserRelation.friend.is_(True),
                ),
            ),
        )
    ).scalar_one()


async def _rank_history_data(db, user_id: int, mode: GameMode) -> list[int]:
    """Last ~90 days of daily global rank (oldest first), gaps carried forward.

    Returns ranks from the first recorded snapshot up to today. Empty if the
    user has no snapshots yet (graph will simply not render).
    """
    today = date.today()
    start = today - timedelta(days=89)
    rows = (
        await db.execute(
            select(UserRankHistory.date, UserRankHistory.rank)
            .where(
                and_(
                    UserRankHistory.user_id == user_id,
                    UserRankHistory.mode == int(mode),
                    UserRankHistory.date >= start,
                ),
            )
            .order_by(UserRankHistory.date),
        )
    ).all()
    by_day = {d: r for d, r in rows if r is not None}
    if not by_day:
        return []
    data: list[int] = []
    last: int | None = None
    cur = min(by_day.keys())
    while cur <= today:
        if cur in by_day:
            last = by_day[cur]
        if last is not None:
            data.append(int(last))
        cur += timedelta(days=1)
    return data


router = APIRouter()


def _mode_to_string(mode: GameMode) -> str:
    """Convert GameMode enum to string."""
    return {
        GameMode.OSU: "osu",
        GameMode.TAIKO: "taiko",
        GameMode.CATCH: "fruits",
        GameMode.MANIA: "mania",
    }.get(mode, "osu")


def _string_to_mode(mode: str) -> GameMode:
    """Convert string to GameMode enum."""
    return {
        "osu": GameMode.OSU,
        "taiko": GameMode.TAIKO,
        "fruits": GameMode.CATCH,
        "mania": GameMode.MANIA,
    }.get(mode, GameMode.OSU)


def _get_user_statistics(user: User, mode: GameMode) -> UserStatisticsResponse:
    """Get user statistics for a specific mode."""
    mode_str = _mode_to_string(mode)

    for stats in user.statistics:
        if stats.mode == mode:
            # User is ranked if they have a global rank
            is_ranked = stats.global_rank is not None

            # Only provide rank_history if user is ranked
            # If is_ranked is True but rank_history is None, the client will show loading
            rank_history = None
            if is_ranked:
                # Provide empty history for now (no historical data yet)
                rank_history = RankHistoryResponse(mode=mode_str, data=[])

            return UserStatisticsResponse(
                ranked_score=stats.ranked_score,
                total_score=stats.total_score,
                pp=stats.pp,
                global_rank=stats.global_rank,
                global_rank_percent=None,  # Would need total player count to calculate
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

    # Return unranked stats for users with no statistics
    return UserStatisticsResponse(
        is_ranked=False,
        rank_history=None,
    )


def _user_to_response(user: User, mode: GameMode | None = None) -> UserResponse:
    """Convert User model to UserResponse."""
    mode = mode or user.playmode
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
        page={"raw": user.user_page or "", "html": user.user_page_html or ""},
        profile_order=(
            user.profile_order.split(",") if user.profile_order else list(PROFILE_SECTIONS)
        ),
        statistics=stats,
    )


@router.get("/users/search", response_model=list[UserCompact])
async def search_users(
    db: DbSession,
    query: str = Query(..., min_length=1, max_length=50),
    limit: int = Query(8, ge=1, le=20),
) -> list[UserCompact]:
    """Search users by (partial) username.

    Declared BEFORE /users/{user_id} so the dynamic route doesn't swallow it.
    Ranking: exact match first, then prefix matches, then shorter names.
    """
    q = query.strip()
    if not q:
        return []
    like = f"%{q}%"
    prefix = f"{q}%"
    rows = (
        (
            await db.execute(
                select(User)
                .where(User.username.ilike(like))
                .order_by(
                    (func.lower(User.username) == q.lower()).desc(),
                    User.username.ilike(prefix).desc(),
                    func.length(User.username),
                    User.username,
                )
                .limit(limit),
            )
        )
        .scalars()
        .all()
    )
    return [
        UserCompact(
            id=u.id,
            username=u.username,
            avatar_url=u.avatar_url,
            country_code=u.country_acronym,
            is_active=u.is_active,
            is_bot=u.is_bot,
            is_supporter=u.is_supporter,
        )
        for u in rows
    ]


@router.get("/users/{user_id}", response_model=UserResponse)
@router.get("/users/{user_id}/", response_model=UserResponse, include_in_schema=False)
async def get_user(
    db: DbSession,
    user_id: int | str,
    key: str | None = Query(None, description="Lookup type: id, username"),
) -> UserResponse:
    """Get a user by ID or username."""
    # Determine lookup method
    if key == "username" or (isinstance(user_id, str) and not user_id.isdigit()):
        result = await db.execute(select(User).where(User.username == str(user_id)))
    else:
        uid = int(user_id) if isinstance(user_id, str) else user_id
        result = await db.execute(select(User).where(User.id == uid))

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    response = _user_to_response(user)
    response.follower_count = await _follower_count(db, user.id)
    if response.statistics and response.statistics.rank_history:
        response.statistics.rank_history.data = await _rank_history_data(
            db, user.id, GameMode.OSU,
        )
    return response


@router.get("/users/{user_id}/monthly-playcounts")
async def get_user_monthly_playcounts(
    db: DbSession,
    user_id: int,
    mode: str | None = Query(None),
) -> list[dict]:
    """Play counts grouped by month (osu! monthly_playcounts shape).

    Computed directly from stored scores. Each entry:
    {"start_date": "YYYY-MM-01", "count": N}.
    """
    mode_map = {"osu": 0, "taiko": 1, "fruits": 2, "mania": 3}
    ruleset_id = mode_map.get(mode) if mode else None

    conds = [Score.user_id == user_id]
    if ruleset_id is not None:
        conds.append(Score.ruleset_id == ruleset_id)

    month = func.date_format(Score.ended_at, "%Y-%m-01").label("month")
    stmt = (
        select(month, func.count(Score.id).label("count"))
        .where(and_(*conds))
        .group_by(month)
        .order_by(month)
    )
    rows = (await db.execute(stmt)).all()
    return [{"start_date": m, "count": int(c)} for m, c in rows if m]


@router.get("/users/{user_id}/{mode}", response_model=UserResponse)
async def get_user_mode(
    db: DbSession,
    user_id: int | str,
    mode: str,
    key: str | None = Query(None),
) -> UserResponse:
    """Get a user by ID or username with specific mode statistics."""
    # Determine lookup method
    if key == "username" or (isinstance(user_id, str) and not user_id.isdigit()):
        result = await db.execute(select(User).where(User.username == str(user_id)))
    else:
        uid = int(user_id) if isinstance(user_id, str) else user_id
        result = await db.execute(select(User).where(User.id == uid))

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    mode_enum = _string_to_mode(mode)
    response = _user_to_response(user, mode_enum)
    response.follower_count = await _follower_count(db, user.id)
    if response.statistics and response.statistics.rank_history:
        response.statistics.rank_history.data = await _rank_history_data(
            db, user.id, mode_enum,
        )
    return response


@router.get("/users/lookup", response_model=UserCompact)
async def lookup_user(
    db: DbSession,
    id: int | None = Query(None),
    username: str | None = Query(None),
) -> UserCompact:
    """Lookup a user by ID or username."""
    if id:
        result = await db.execute(select(User).where(User.id == id))
    elif username:
        result = await db.execute(select(User).where(User.username == username))
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide id or username",
        )

    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserCompact(
        id=user.id,
        username=user.username,
        avatar_url=user.avatar_url,
        country_code=user.country_acronym,
        is_active=user.is_active,
        is_bot=user.is_bot,
        is_admin=privileges.is_admin(user.privileges),
        is_staff=privileges.is_staff(user.privileges),
        is_supporter=user.is_supporter,
    )
