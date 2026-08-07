"""Simple role checks for split groups (trips)."""

from __future__ import annotations

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)

_ROLE_RANK = {
    ROLE_MEMBER: 1,
    ROLE_ADMIN: 2,
    ROLE_OWNER: 3,
}

# Minimum role required for each action.
PERM_VIEW_GROUP = "view_group"
PERM_ADD_EXPENSE = "add_expense"
PERM_VIEW_EXPENSE = "view_expense"
PERM_VIEW_SETTLEMENT = "view_settlement"
PERM_VIEW_ACTIVITY = "view_activity"
PERM_MANAGE_SETTINGS = "manage_settings"
PERM_MANAGE_MEMBERS = "manage_members"
PERM_ARCHIVE_GROUP = "archive_group"
PERM_TRANSFER_OWNERSHIP = "transfer_ownership"

_MIN_ROLE = {
    PERM_VIEW_GROUP: ROLE_MEMBER,
    PERM_ADD_EXPENSE: ROLE_MEMBER,
    PERM_VIEW_EXPENSE: ROLE_MEMBER,
    PERM_VIEW_SETTLEMENT: ROLE_MEMBER,
    PERM_VIEW_ACTIVITY: ROLE_MEMBER,
    PERM_MANAGE_SETTINGS: ROLE_ADMIN,
    PERM_MANAGE_MEMBERS: ROLE_ADMIN,
    PERM_ARCHIVE_GROUP: ROLE_OWNER,
    PERM_TRANSFER_OWNERSHIP: ROLE_OWNER,
}


def normalize_role(role: str | None) -> str:
    if role in ROLES:
        return role
    return ROLE_MEMBER


def role_rank(role: str | None) -> int:
    return _ROLE_RANK.get(normalize_role(role), 0)


def role_at_least(role: str | None, minimum: str) -> bool:
    return role_rank(role) >= role_rank(minimum)


def can(role: str | None, permission: str) -> bool:
    minimum = _MIN_ROLE.get(permission)
    if minimum is None:
        return False
    return role_at_least(role, minimum)


def can_assign_role(actor_role: str | None, new_role: str) -> bool:
    """Admins and owners may set member/admin; only transfer sets owner."""
    if new_role == ROLE_OWNER:
        return False
    if new_role not in (ROLE_MEMBER, ROLE_ADMIN):
        return False
    return role_at_least(actor_role, ROLE_ADMIN)


def can_remove_member(actor_role: str | None, target_role: str | None) -> bool:
    if normalize_role(target_role) == ROLE_OWNER:
        return False
    return role_at_least(actor_role, ROLE_ADMIN)
