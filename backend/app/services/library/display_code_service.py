"""Display code generation service using database counters."""

from sqlalchemy import text

from app.db.session import write_scope


async def generate_display_code(team_id: int, prefix: str) -> str:
    """Generate next display code like P-202603001, S-202603001-001, etc."""
    # next_display_code (migration 110) INSERT/UPDATEs display_code_counters
    # and RETURNs the code — a writing proc, so it must commit (write_scope),
    # otherwise the counter increment rolls back and codes collide.
    async with write_scope() as session:
        result = await session.execute(
            text("SELECT public.next_display_code(:p_team_id, :p_prefix)"),
            {"p_team_id": team_id, "p_prefix": prefix},
        )
        return result.scalar()
