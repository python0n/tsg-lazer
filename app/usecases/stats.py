"""Recompute a user's profile statistics (pp, accuracy, ranks) after a score.

Follows the standard osu! model: profile pp is a weighted sum of the user's best
score per beatmap (0.95^i, sorted by pp desc) plus a small bonus; profile accuracy
is the weighted average over those same top scores; global/country ranks are derived
by counting users with strictly higher pp.
"""

import logging

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import select

from app.models.score import Score
from app.models.user import GameMode
from app.models.user import User
from app.models.user import UserStatistics

logger = logging.getLogger(__name__)

WEIGHT = 0.95
TOP_SCORES = 100


async def update_user_statistics(
    db,
    user: User,
    mode: GameMode,
    *,
    increment_playcount: bool = True,
) -> UserStatistics:
    """Recompute and persist the user's statistics for one mode."""
    result = await db.execute(
        select(UserStatistics).where(
            and_(
                UserStatistics.user_id == user.id,
                UserStatistics.mode == mode,
            ),
        ),
    )
    stats = result.scalar_one_or_none()
    if stats is None:
        stats = UserStatistics(user_id=user.id, mode=mode)
        db.add(stats)
        await db.flush()

    rn = (
        func.row_number()
        .over(
            partition_by=Score.beatmap_id,
            order_by=(Score.pp.desc(), Score.id.asc()),
        )
        .label("rn")
    )
    best_subq = (
        select(Score.id.label("id"), rn)
        .where(
            and_(
                Score.user_id == user.id,
                Score.ruleset_id == int(mode),
                Score.passed.is_(True),
                Score.ranked.is_(True),
            ),
        )
        .subquery()
    )
    best_query = (
        select(Score)
        .join(best_subq, and_(best_subq.c.id == Score.id, best_subq.c.rn == 1))
        .order_by(Score.pp.desc())
    )
    best_scores = (await db.execute(best_query)).scalars().all()

    top = best_scores[:TOP_SCORES]
    weighted_pp = 0.0
    weighted_acc = 0.0
    weight_sum = 0.0
    for i, s in enumerate(top):
        w = WEIGHT**i
        weighted_pp += (s.pp or 0.0) * w
        weighted_acc += (s.accuracy or 0.0) * w
        weight_sum += w

    bonus_pp = 416.6667 * (1 - 0.9994 ** len(best_scores))
    stats.pp = round(weighted_pp + bonus_pp, 3)

    if weight_sum > 0:
        acc_percent = round((weighted_acc / weight_sum) * 100, 2)
        stats.accuracy = acc_percent
        stats.hit_accuracy = acc_percent

    stats.ranked_score = sum(int(s.total_score or 0) for s in best_scores)
    if best_scores:
        stats.maximum_combo = max(int(s.max_combo or 0) for s in best_scores)

    if increment_playcount:
        stats.play_count = (stats.play_count or 0) + 1

    await db.flush()

    global_rank = (
        await db.execute(
            select(func.count(UserStatistics.id) + 1).where(
                and_(
                    UserStatistics.mode == mode,
                    UserStatistics.pp > stats.pp,
                ),
            ),
        )
    ).scalar_one()
    stats.global_rank = global_rank

    country_rank = (
        await db.execute(
            select(func.count(UserStatistics.id) + 1)
            .join(User, User.id == UserStatistics.user_id)
            .where(
                and_(
                    UserStatistics.mode == mode,
                    UserStatistics.pp > stats.pp,
                    User.country_acronym == user.country_acronym,
                ),
            ),
        )
    ).scalar_one()
    stats.country_rank = country_rank

    await db.flush()
    logger.info(
        "Updated stats user=%s mode=%s pp=%.2f acc=%.2f rank=%s",
        user.id,
        int(mode),
        stats.pp,
        stats.hit_accuracy,
        stats.global_rank,
    )
    return stats
