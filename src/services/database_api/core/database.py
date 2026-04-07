import asyncpg
from contextlib import asynccontextmanager
from core.config import settings

_pool: asyncpg.Pool | None = None

async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.db_dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )

async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()

@asynccontextmanager
async def get_connection():
    """Context manager para garantir que a conexão é sempre libertada."""
    async with _pool.acquire() as conn:
        yield conn