"""Server-wide statistics (for the website home page)."""

from datetime import datetime
from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func
from sqlalchemy import select

from app.api.deps import DbSession
from app.models.beatmap import Beatmap
from app.models.beatmap import BeatmapSet
from app.models.score import Score
from app.models.user import User

router = APIRouter()

# A user counts as "online" if seen within this window (proxy via last_visit).
ONLINE_WINDOW_MINUTES = 5


@router.get("/server/stats")
async def server_stats(db: DbSession) -> dict:
    """Aggregate counts for the front page tiles."""
    registered_users = (
        await db.execute(select(func.count(User.id)))
    ).scalar_one()

    cutoff = datetime.utcnow() - timedelta(minutes=ONLINE_WINDOW_MINUTES)
    online_users = (
        await db.execute(
            select(func.count(User.id)).where(User.last_visit >= cutoff),
        )
    ).scalar_one()

    total_scores = (
        await db.execute(select(func.count(Score.id)))
    ).scalar_one()

    total_beatmaps = (
        await db.execute(select(func.count(Beatmap.id)))
    ).scalar_one()

    total_beatmapsets = (
        await db.execute(select(func.count(BeatmapSet.id)))
    ).scalar_one()

    return {
        "registered_users": registered_users,
        "online_users": online_users,
        "total_scores": total_scores,
        "total_beatmaps": total_beatmaps,
        "total_beatmapsets": total_beatmapsets,
    }
