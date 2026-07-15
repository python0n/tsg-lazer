"""Global rankings (leaderboards) endpoints."""

from fastapi import APIRouter
from fastapi import Query
from fastapi import Request
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import DbSession
from app.api.v2.schemas import UserCompact
from app.api.v2.users import _get_user_statistics
from app.api.v2.users import _string_to_mode
from app.models.user import User
from app.models.user import UserStatistics

router = APIRouter()

PAGE_SIZE = 50


async def _country_rankings(db: DbSession, game_mode, page: int) -> dict:
    """Per-country aggregated rankings (osu! CountryStatistics shape)."""
    conditions = [
        UserStatistics.mode == game_mode,
        UserStatistics.pp > 0,
        User.is_bot.is_(False),
        User.is_restricted.is_(False),
    ]

    count_stmt = (
        select(func.count(func.distinct(User.country_acronym)))
        .select_from(UserStatistics)
        .join(User, User.id == UserStatistics.user_id)
        .where(*conditions)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(
            User.country_acronym.label("code"),
            func.count(func.distinct(User.id)).label("active_users"),
            func.coalesce(func.sum(UserStatistics.play_count), 0).label("play_count"),
            func.coalesce(func.sum(UserStatistics.ranked_score), 0).label(
                "ranked_score",
            ),
            func.coalesce(func.sum(UserStatistics.pp), 0).label("performance"),
        )
        .join(UserStatistics, UserStatistics.user_id == User.id)
        .where(*conditions)
        .group_by(User.country_acronym)
        .order_by(func.sum(UserStatistics.pp).desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    rows = (await db.execute(stmt)).all()

    ranking = [
        {
            "code": row.code,
            "active_users": int(row.active_users),
            "play_count": int(row.play_count),
            "ranked_score": int(row.ranked_score),
            "performance": int(round(row.performance)),
        }
        for row in rows
    ]

    cursor = {"page": page + 1} if page * PAGE_SIZE < total else None
    return {"ranking": ranking, "cursor": cursor, "total": total}


@router.get("/rankings/{mode}/{ranking_type}")
@router.get("/rankings/{mode}/{ranking_type}/", include_in_schema=False)
async def get_rankings(
    db: DbSession,
    request: Request,
    mode: str,
    ranking_type: str,
    country: str | None = Query(None),
    filter: str | None = Query(None),  # noqa: A002 (osu! API uses "filter")
) -> dict:
    """Global/country performance (or score) rankings.

    Mirrors osu!-API GET /rankings/{mode}/{type}.
    """
    game_mode = _string_to_mode(mode)

    raw_page = request.query_params.get("cursor[page]") or request.query_params.get(
        "page",
    )
    page = int(raw_page) if (raw_page and raw_page.isdigit()) else 1
    page = max(page, 1)

    if ranking_type == "country":
        return await _country_rankings(db, game_mode, page)

    if ranking_type == "score":
        order_col = UserStatistics.ranked_score
        ranked_filter = UserStatistics.ranked_score > 0
    else:
        order_col = UserStatistics.pp
        ranked_filter = UserStatistics.pp > 0

    conditions = [
        UserStatistics.mode == game_mode,
        ranked_filter,
        User.is_bot.is_(False),
        User.is_restricted.is_(False),
    ]
    if country:
        conditions.append(User.country_acronym == country.upper())

    count_stmt = (
        select(func.count())
        .select_from(UserStatistics)
        .join(User, User.id == UserStatistics.user_id)
        .where(*conditions)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(User)
        .join(UserStatistics, UserStatistics.user_id == User.id)
        .where(*conditions)
        .options(selectinload(User.statistics))
        .order_by(order_col.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    users = (await db.execute(stmt)).scalars().all()

    ranking = []
    for user in users:
        stats = _get_user_statistics(user, game_mode).model_dump(by_alias=True)
        stats["user"] = UserCompact(
            id=user.id,
            username=user.username,
            avatar_url=user.avatar_url,
            country_code=user.country_acronym,
            is_active=user.is_active,
            is_bot=user.is_bot,
            is_supporter=user.is_supporter,
        ).model_dump(by_alias=True)
        ranking.append(stats)

    cursor = {"page": page + 1} if page * PAGE_SIZE < total else None
    return {"ranking": ranking, "cursor": cursor, "total": total}
