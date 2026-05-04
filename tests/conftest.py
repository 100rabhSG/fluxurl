"""
Shared test fixtures.

Key idea: we replace the real Postgres session with an in-memory SQLite session
so tests are fast, isolated, and don't need Docker running.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.session import get_session
from app.main import app
from app.models import Base

# --- In-memory SQLite engine (async via aiosqlite) ---
# "check_same_thread=False" is required for SQLite when used from async code.
TEST_DATABASE_URL = "sqlite+aiosqlite:///"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionFactory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture(autouse=True)
async def setup_database():
    """Create all tables before each test, drop them after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def session():
    """Yield a test DB session."""
    async with TestSessionFactory() as session:
        yield session


@pytest.fixture
async def client():
    """HTTP test client that talks to the app with the test DB swapped in."""

    async def _override_get_session():
        async with TestSessionFactory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
