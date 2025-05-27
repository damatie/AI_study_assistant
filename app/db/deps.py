# db.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL")

engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # for debug; disable in production
    future=True,
)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# Dependency
async def get_db():
    """
    Dependency that yields a new AsyncSession per request.
    The session is closed automatically when the request is done.
    """
    async with AsyncSessionLocal() as session:
            yield session
        
