"""Recompute a user's profile statistics (pp, accuracy, ranks) after a score.

Follows the standard osu! model: profile pp is a weighted sum of the user's best
score per beatmap (0.95^i, sorted by pp desc) plus a small bonus; profile accuracy
is the weighted average over those same top scores; global/country ranks are derived
by counting users with strictly higher pp.
"""

import json
from datetime import date
import logging

from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.score import Score
from app.models.user import GameMode
from app.models.user import User
from app.models.user import UserRankHistory
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
    # Fetch or create the stats row for (user, mode)
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

    # Best score per beatmap (passed + ranked), ordered by pp desc
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

    # Weighted pp + accuracy over the top scores
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
        # Score.accuracy is a 0-1 fraction; profile accuracy is a 0-100 percent
        acc_percent = round((weighted_acc / weight_sum) * 100, 2)
        stats.accuracy = acc_percent
        stats.hit_accuracy = acc_percent

    stats.ranked_score = sum(int(s.total_score or 0) for s in best_scores)
    if best_scores:
        stats.maximum_combo = max(int(s.max_combo or 0) for s in best_scores)

    # Aggregate totals over ALL scores for this mode (every play counts), not just
    # best-per-beatmap. Recomputed from scratch each time, so it stays idempotent.
    all_scores = (
        await db.execute(
            select(Score)
            .options(selectinload(Score.beatmap))
            .where(
                and_(
                    Score.user_id == user.id,
                    Score.ruleset_id == int(mode),
                ),
            ),
        )
    ).scalars().all()

    total_score = 0
    total_hits = 0
    play_time = 0
    for sc in all_scores:
        total_score += int(sc.total_score or 0)
        try:
            sdata = json.loads(sc.data) if sc.data else {}
            st = sdata.get("statistics", {})
            total_hits += (
                int(st.get("great", 0)) + int(st.get("ok", 0)) + int(st.get("meh", 0))
            )
        except (ValueError, TypeError):
            pass
        if sc.beatmap is not None:
            play_time += int(sc.beatmap.total_length or 0)

    stats.total_score = total_score
    stats.total_hits = total_hits
    stats.play_time = play_time
    stats.play_count = len(all_scores)

    # Grade counts over best-per-beatmap (osu! semantics: best grade per map).
    grade_field = {"X": "grade_ss", "XH": "grade_ssh", "S": "grade_s", "SH": "grade_sh", "A": "grade_a"}
    counts = {"grade_ss": 0, "grade_ssh": 0, "grade_s": 0, "grade_sh": 0, "grade_a": 0}
    for sc in best_scores:
        field = grade_field.get((sc.rank or "").upper())
        if field:
            counts[field] += 1
    stats.grade_ss = counts["grade_ss"]
    stats.grade_ssh = counts["grade_ssh"]
    stats.grade_s = counts["grade_s"]
    stats.grade_sh = counts["grade_sh"]
    stats.grade_a = counts["grade_a"]

    await db.flush()

    # Global rank: users (same mode) with strictly higher pp, +1
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

    # Country rank: same, restricted to the user's country
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

    # Record/refresh today's rank snapshot (one row per user/mode/day) so the
    # profile rank graph can grow over time. Idempotent within a single day.
    today = date.today()
    existing_snap = (
        await db.execute(
            select(UserRankHistory).where(
                and_(
                    UserRankHistory.user_id == user.id,
                    UserRankHistory.mode == int(mode),
                    UserRankHistory.date == today,
                ),
            ),
        )
    ).scalar_one_or_none()
    if existing_snap is None:
        db.add(
            UserRankHistory(
                user_id=user.id,
                mode=int(mode),
                date=today,
                rank=stats.global_rank,
                pp=stats.pp,
            ),
        )
    else:
        existing_snap.rank = stats.global_rank
        existing_snap.pp = stats.pp

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
