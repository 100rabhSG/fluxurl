from fastapi import Depends, FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import Url

app = FastAPI(title="Fluxurl")


@app.get("/")
async def root() -> dict[str, str]:
    return {"hello": "world"}


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"db": "ok"}


# TODO: remove in Sub-session 3 once /shorten exists. Temporary proof
# that the ORM + Alembic migration round-trip works end-to-end.
@app.get("/debug/urls/count")
async def debug_urls_count(
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    result = await session.execute(select(func.count()).select_from(Url))
    count = result.scalar_one()
    return {"count": count}
