from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.api.v1.routes.auth.auth import get_current_user
from app.api.v1.routes.router import router as api_router
from app.db.deps import Base, get_db
from app.models.user import Role, User


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Ensure pytest-anyio uses asyncio for all async tests."""
    return "asyncio"


@pytest.fixture(scope="session")
def test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture(scope="session")
def test_db_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    db_path = tmp_path_factory.mktemp("data") / "test_admin.sqlite"
    return f"sqlite+aiosqlite:///{db_path}"


@pytest_asyncio.fixture(scope="session")
async def engine(test_db_url: str):
    engine = create_async_engine(test_db_url, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture()
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture()
async def client(test_app: FastAPI, db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def _get_test_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def _get_admin_user():
        result = await db_session.execute(select(User).where(User.role == Role.admin).limit(1))
        user = result.scalar_one_or_none()
        if user is None:
            return SimpleNamespace(id="admin", role=Role.admin)
        return user

    test_app.dependency_overrides[get_db] = _get_test_db
    test_app.dependency_overrides[get_current_user] = _get_admin_user

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    test_app.dependency_overrides.clear()
