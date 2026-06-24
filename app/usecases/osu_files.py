"""Fetch and cache individual .osu beatmap files for PP calculation.

The lazer client never uploads the .osu file, so we fetch the raw difficulty
from osu.ppy.sh/osu/{beatmap_id} (public, no auth) and cache it on disk under
settings.beatmaps_path. Returns the local path, or None on failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_OSU_RAW_URL = "https://osu.ppy.sh/osu/{beatmap_id}"


async def ensure_osu_file(beatmap_id: int) -> str | None:
    """Return a local path to {beatmap_id}.osu, downloading + caching if needed."""
    settings = get_settings()
    beatmaps_dir = Path(settings.beatmaps_path)
    beatmaps_dir.mkdir(parents=True, exist_ok=True)

    path = beatmaps_dir / f"{beatmap_id}.osu"
    if path.exists() and path.stat().st_size > 0:
        return str(path)

    url = _OSU_RAW_URL.format(beatmap_id=beatmap_id)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code != 200 or not response.content:
                logger.warning(
                    "Could not fetch .osu for beatmap %s (status %s)",
                    beatmap_id,
                    response.status_code,
                )
                return None
            path.write_bytes(response.content)
            return str(path)
    except Exception:
        logger.exception("Error downloading .osu for beatmap %s", beatmap_id)
        return None
