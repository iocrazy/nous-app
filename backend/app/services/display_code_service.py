"""Display code generation service using database counters."""

from app.db.supabase_client import get_async_supabase_admin


async def generate_display_code(team_id: int, prefix: str) -> str:
    """Generate next display code like P-202603001, S-202603001-001, etc."""
    client = await get_async_supabase_admin()
    result = await client.rpc(
        "next_display_code",
        {"p_team_id": team_id, "p_prefix": prefix},
    ).execute()
    return result.data
