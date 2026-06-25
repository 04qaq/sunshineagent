"""Async database engine and session factory setup."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config.config import get_config


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            config = get_config()
            db_path = str(config.state_db_path)

        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            echo=False,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @property
    def engine(self):
        return self._engine

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def init(self):
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self):
        await self._engine.dispose()
