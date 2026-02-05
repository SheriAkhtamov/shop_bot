from typing import Generic, TypeVar, Type, Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.core import Base

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    def __init__(self, session: AsyncSession, model: Type[T]):
        self.session = session
        self.model = model

    async def get_by_id(self, id: int) -> Optional[T]:
        stmt = select(self.model).where(self.model.id == id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_all(self) -> List[T]:
        stmt = select(self.model)
        return (await self.session.execute(stmt)).scalars().all()

    def add(self, obj: T):
        self.session.add(obj)

    async def delete(self, obj: T):
        await self.session.delete(obj)
        
    async def commit(self):
        await self.session.commit()
