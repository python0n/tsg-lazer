"""Recompute every user's profile statistics from their stored scores.

When to use:
  - After importing or migrating scores from another source.
  - After changing the statistics logic in `app/usecases/stats.py`, to backfill
    existing rows (stats are otherwise only recomputed on the next submission).
  - Any time profile totals look out of sync with the actual scores.

What it does:
  For every (user, mode) pair that has at least one score, it calls
  `update_user_statistics(...)`, which recomputes pp, accuracy, ranks,
  ranked/total score, total hits, play count, play time and grade counts
  from scratch. The operation is idempotent — running it twice is harmless.

How to run (inside the running container, so it uses the app's DB config):

    docker compose exec -T tsg-lazer python - < scripts/recompute_stats.py

It is read-only with respect to scores; it only writes the user_statistics rows.
"""

import asyncio

from sqlalchemy import select

from app.core.database import async_session_maker

# Import all models with relationships so SQLAlchemy can resolve mappers
# (Score -> Beatmap) when this runs outside the normal app startup path.
from app.models.beatmap import Beatmap  # noqa: F401
from app.models.beatmap import BeatmapSet  # noqa: F401
from app.models.score import Score
from app.models.user import GameMode
from app.models.user import User
from app.models.user import UserStatistics  # noqa: F401
from app.usecases.stats import update_user_statistics


async def main() -> None:
    async with async_session_maker() as db:
        pairs = (
            await db.execute(select(Score.user_id, Score.ruleset_id).distinct())
        ).all()
        print(f"Found {len(pairs)} (user, mode) pair(s) with scores")

        updated = 0
        for user_id, ruleset_id in pairs:
            user = (
                await db.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user is None:
                continue
            try:
                mode = GameMode(ruleset_id)
            except ValueError:
                continue
            stats = await update_user_statistics(
                db, user, mode, increment_playcount=False
            )
            updated += 1
            print(
                f"  user={user_id} mode={ruleset_id} -> "
                f"pp={stats.pp} play_count={stats.play_count} "
                f"total_score={stats.total_score} total_hits={stats.total_hits} "
                f"play_time={stats.play_time}s "
                f"grades ss/ssh/s/sh/a="
                f"{stats.grade_ss}/{stats.grade_ssh}/{stats.grade_s}/"
                f"{stats.grade_sh}/{stats.grade_a}"
            )
        await db.commit()
    print(f"Done. Recomputed {updated} statistics row(s).")


if __name__ == "__main__":
    asyncio.run(main())
