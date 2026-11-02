"""Which phone a call is transferred to: a fixed number, or the customer's branch manager."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ymva.models import Branch
from ymva.voice.config import AgentConfig

BRANCH_DETAIL = "branch"


def _key(name: str) -> str:
    """'Chintamani ', 'CHINTAMANI' and 'chinta-mani' all match."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


async def find_branch(db: AsyncSession, name: str | None) -> Branch | None:
    wanted = _key(name or "")
    if not wanted:
        return None
    for branch in await db.scalars(select(Branch)):
        if wanted in {_key(n) for n in [branch.name, *branch.other_names]}:
            return branch
    return None


async def transfer_number(
    db: AsyncSession, config: AgentConfig, variables: dict[str, str]
) -> str | None:
    """Where this call's transfer goes, or None when the agent doesn't transfer. A customer
    whose branch isn't listed goes to the agent's fixed number, if it has one."""
    transfer = config.transfer
    if not transfer.enabled:
        return None
    if transfer.to == "branch_manager":
        branch = await find_branch(db, variables.get(BRANCH_DETAIL))
        if branch is not None:
            return branch.manager_phone
    return transfer.number
