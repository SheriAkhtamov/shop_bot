from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False, # echo=True для отладки SQL
    pool_size=20,
    max_overflow=10,
)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

# Функция для получения сессии в FastAPI
async def get_db():
    async with async_session_maker() as session:
        yield session
