"""What each role may do. The server checks these on every request; the dashboard reads the
signed-in user's list from /auth/me to hide pages and buttons they can't use."""

from enum import StrEnum

from ymva.models import Role


class Perm(StrEnum):
    AGENTS_VIEW = "agents.view"
    AGENTS_EDIT = "agents.edit"
    CALLS_PLACE = "calls.place"  # call a phone / web test call, hang up calls they can see
    CALLS_VIEW_ALL = "calls.view_all"  # without it, only calls the user placed
    CALLS_EDIT = "calls.edit"  # outcome and details
    RECORDINGS_LISTEN = "recordings.listen"
    RECORDINGS_DOWNLOAD = "recordings.download"
    COSTS_VIEW = "costs.view"
    CAMPAIGNS_VIEW = "campaigns.view"
    CAMPAIGNS_MANAGE = "campaigns.manage"
    NUMBERS_VIEW = "numbers.view"
    NUMBERS_MANAGE = "numbers.manage"
    TOOLS_MANAGE = "tools.manage"
    INTEGRATIONS_MANAGE = "integrations.manage"
    SETTINGS_MANAGE = "settings.manage"  # alerts, system health
    DNC_MANAGE = "dnc.manage"
    USERS_MANAGE = "users.manage"
    OWNERS_MANAGE = "owners.manage"
    AUDIT_VIEW = "audit.view"


P = Perm
ROLE_PERMS: dict[Role, frozenset[Perm]] = {
    Role.OWNER: frozenset(Perm),
    Role.ADMIN: frozenset(Perm) - {P.OWNERS_MANAGE},
    Role.CAMPAIGN_MANAGER: frozenset({
        P.AGENTS_VIEW, P.CALLS_PLACE, P.CALLS_VIEW_ALL, P.CALLS_EDIT, P.RECORDINGS_LISTEN,
        P.COSTS_VIEW, P.CAMPAIGNS_VIEW, P.CAMPAIGNS_MANAGE, P.NUMBERS_VIEW,
    }),
    Role.CALLER: frozenset({P.CALLS_PLACE, P.RECORDINGS_LISTEN, P.NUMBERS_VIEW}),
    Role.REVIEWER: frozenset({
        P.CALLS_VIEW_ALL, P.CALLS_EDIT, P.RECORDINGS_LISTEN, P.RECORDINGS_DOWNLOAD,
    }),
    Role.VIEWER: frozenset({P.AGENTS_VIEW, P.CALLS_VIEW_ALL, P.COSTS_VIEW, P.CAMPAIGNS_VIEW}),
}  # fmt: skip


def can(role: Role, perm: Perm) -> bool:
    return perm in ROLE_PERMS[role]
