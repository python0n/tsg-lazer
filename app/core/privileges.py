"""User privilege bitflags (mirrors bancho.py / Shiina semantics).

Stored as an integer in users.privileges. Use the helpers to test membership.
"""

from enum import IntFlag


class Privileges(IntFlag):
    UNRESTRICTED = 1 << 0
    VERIFIED = 1 << 1
    WHITELISTED = 1 << 2
    SUPPORTER = 1 << 4
    PREMIUM = 1 << 5
    ALUMNI = 1 << 7
    TOURNEY_MANAGER = 1 << 10
    NOMINATOR = 1 << 11
    MODERATOR = 1 << 12
    ADMINISTRATOR = 1 << 13
    DEVELOPER = 1 << 14

    STAFF = MODERATOR | ADMINISTRATOR | DEVELOPER


# A freshly registered, normal account.
DEFAULT_PRIVILEGES = int(Privileges.UNRESTRICTED | Privileges.VERIFIED)  # 3


def has_privilege(privileges: int, flag: Privileges) -> bool:
    """True if `privileges` contains every bit of `flag`."""
    return (privileges & int(flag)) == int(flag)


def is_admin(privileges: int) -> bool:
    return has_privilege(privileges, Privileges.ADMINISTRATOR)


def is_staff(privileges: int) -> bool:
    """Any of MODERATOR / ADMINISTRATOR / DEVELOPER."""
    return bool(privileges & int(Privileges.STAFF))
