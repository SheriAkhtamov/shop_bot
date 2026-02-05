from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import Order
from .base import BaseRepository

class OrderRepository(BaseRepository[Order]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Order)

    async def get_full_info(self, order_id: int) -> Optional[Order]:
        stmt = select(Order).options(
            selectinload(Order.user),
            selectinload(Order.items)
        ).where(Order.id == order_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_user(self, user_id: int) -> List[Order]:
        stmt = select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
        return (await self.session.execute(stmt)).scalars().all()
        
    async def get_all_detailed(self, limit: int = 50, offset: int = 0) -> List[Order]:
        stmt = select(Order).options(selectinload(Order.user)).order_by(Order.created_at.desc()).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()
        
    async def count(self) -> int:
        from sqlalchemy import func
        stmt = select(func.count(Order.id))
        return (await self.session.execute(stmt)).scalar()

    async def get_with_lock(self, order_id: int) -> Optional[Order]:
         stmt = select(Order).options(selectinload(Order.user)).where(Order.id == order_id).with_for_update()
         return (await self.session.execute(stmt)).scalar_one_or_none()
