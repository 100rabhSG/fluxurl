from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import urls as urls_router
from app.db.session import get_session

app = FastAPI(title="Fluxurl")
app.include_router(urls_router.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"hello": "world"}


@app.get("/healthz")
async def healthz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"db": "ok"}

