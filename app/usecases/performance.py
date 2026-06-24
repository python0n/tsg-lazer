"""Server-side PP calculation for tsg-lazer using akatsuki-pp-py.

Lazer sends mods as acronyms (e.g. [{"acronym": "HD"}]) and hit results as a
statistics dict (e.g. {"great": 300, "ok": 12, ...}). akatsuki-pp-py wants a
stable mod bitmask and n300/n100/n50/ngeki/nkatu/nmiss counts, so this module
provides the two adapters plus the calculation entrypoint.

RX/AP do not need separate logic: akatsuki-pp-py computes relax/autopilot pp
itself as long as the RX (128) / AP (8192) bit is set in the bitmask.
"""

from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


# ---- Stable mod bitmask values (osu! "legacy" mods) ----
class Mods:
    NOMOD = 0
    NOFAIL = 1 << 0
    EASY = 1 << 1
    TOUCHDEVICE = 1 << 2
    HIDDEN = 1 << 3
    HARDROCK = 1 << 4
    SUDDENDEATH = 1 << 5
    DOUBLETIME = 1 << 6
    RELAX = 1 << 7
    HALFTIME = 1 << 8
    NIGHTCORE = 1 << 9
    FLASHLIGHT = 1 << 10
    AUTOPLAY = 1 << 11
    SPUNOUT = 1 << 12
    AUTOPILOT = 1 << 13
    PERFECT = 1 << 14
    KEY4 = 1 << 15
    KEY5 = 1 << 16
    KEY6 = 1 << 17
    KEY7 = 1 << 18
    KEY8 = 1 << 19
    FADEIN = 1 << 20
    RANDOM = 1 << 21
    CINEMA = 1 << 22
    KEY9 = 1 << 24
    KEY1 = 1 << 26
    KEY3 = 1 << 27
    KEY2 = 1 << 28
    MIRROR = 1 << 30


# Lazer acronym -> stable bitmask. Lazer-only mods (CL, AC, MU, NS, ...) have no
# stable bit and are simply skipped (they don't affect pp here).
_ACRONYM_TO_MOD: dict[str, int] = {
    "NF": Mods.NOFAIL,
    "EZ": Mods.EASY,
    "TD": Mods.TOUCHDEVICE,
    "HD": Mods.HIDDEN,
    "HR": Mods.HARDROCK,
    "SD": Mods.SUDDENDEATH,
    "DT": Mods.DOUBLETIME,
    "RX": Mods.RELAX,
    "HT": Mods.HALFTIME,
    "DC": Mods.HALFTIME,  # Daycore -> treat as HalfTime for pp
    "NC": Mods.NIGHTCORE,
    "FL": Mods.FLASHLIGHT,
    "AT": Mods.AUTOPLAY,
    "SO": Mods.SPUNOUT,
    "AP": Mods.AUTOPILOT,
    "PF": Mods.PERFECT,
    "FI": Mods.FADEIN,
    "RD": Mods.RANDOM,
    "CN": Mods.CINEMA,
    "MR": Mods.MIRROR,
    "1K": Mods.KEY1,
    "2K": Mods.KEY2,
    "3K": Mods.KEY3,
    "4K": Mods.KEY4,
    "5K": Mods.KEY5,
    "6K": Mods.KEY6,
    "7K": Mods.KEY7,
    "8K": Mods.KEY8,
    "9K": Mods.KEY9,
}


def mods_to_bitmask(mods: list[dict]) -> int:
    """Convert a lazer mods list ([{'acronym': 'HD'}, ...]) to a stable bitmask."""
    bitmask = 0
    for mod in mods:
        acronym = (mod.get("acronym") or "").upper()
        bit = _ACRONYM_TO_MOD.get(acronym)
        if bit is not None:
            bitmask |= bit
    # rosu/akatsuki-pp ignores NC on its own and expects DT to be set alongside
    if bitmask & Mods.NIGHTCORE:
        bitmask |= Mods.DOUBLETIME
    return bitmask


def statistics_to_counts(mode: int, stats: dict) -> dict[str, int]:
    """Convert a lazer statistics dict to akatsuki-pp hit counts, per ruleset.

    mode: 0=osu, 1=taiko, 2=catch, 3=mania
    """
    g = lambda k: int(stats.get(k, 0) or 0)  # noqa: E731

    if mode == 3:  # mania
        return {
            "n300": g("great"),
            "n100": g("ok"),
            "n50": g("meh"),
            "ngeki": g("perfect"),  # 320 / MAX
            "nkatu": g("good"),     # 200
            "nmiss": g("miss"),
        }
    if mode == 2:  # catch
        return {
            "n300": g("great"),            # caught fruit
            "n100": g("large_tick_hit"),   # caught drop
            "n50": g("small_tick_hit"),    # caught droplet
            "ngeki": 0,
            "nkatu": g("small_tick_miss"), # missed droplet
            "nmiss": g("miss"),
        }
    if mode == 1:  # taiko
        return {
            "n300": g("great"),
            "n100": g("ok"),
            "n50": 0,
            "ngeki": 0,
            "nkatu": 0,
            "nmiss": g("miss"),
        }
    # osu! standard (default)
    return {
        "n300": g("great"),
        "n100": g("ok"),
        "n50": g("meh"),
        "ngeki": 0,
        "nkatu": 0,
        "nmiss": g("miss"),
    }


def calculate_pp(
    osu_file_path: str,
    mode: int,
    mods: list[dict],
    statistics: dict,
    combo: int | None,
) -> tuple[float, float]:
    """Calculate (pp, star_rating) for a single score. Synchronous (CPU-bound).

    Returns (0.0, 0.0) on any failure so submission never breaks.
    """
    try:
        from akatsuki_pp_py import Beatmap
        from akatsuki_pp_py import Calculator

        bitmask = mods_to_bitmask(mods)
        counts = statistics_to_counts(mode, statistics)

        bmap = Beatmap(path=osu_file_path)
        calculator = Calculator(
            mode=mode,
            mods=bitmask,
            combo=combo,
            n300=counts["n300"],
            n100=counts["n100"],
            n50=counts["n50"],
            n_geki=counts["ngeki"],
            n_katu=counts["nkatu"],
            n_misses=counts["nmiss"],
        )
        result = calculator.performance(bmap)

        pp = result.pp
        if math.isnan(pp) or math.isinf(pp):
            pp = 0.0
        else:
            pp = round(pp, 3)

        stars = result.difficulty.stars
        return pp, stars
    except Exception:
        logger.exception("PP calculation failed for %s", osu_file_path)
        return 0.0, 0.0
