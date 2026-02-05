from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import CartItem
from .base import BaseRepository

class CartRepository(BaseRepository[CartItem]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, CartItem)

    async def get_by_user(self, user_id: int) -> List[CartItem]:
        stmt = select(CartItem).options(selectinload(CartItem.product)).where(CartItem.user_id == user_id).order_by(CartItem.id)
        return (await self.session.execute(stmt)).scalars().all()
        
    async def get_item(self, user_id: int, product_id: int) -> Optional[CartItem]:
        stmt = select(CartItem).where(CartItem.user_id == user_id, CartItem.product_id == product_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
        
    async def get_by_id_and_user(self, item_id: int, user_id: int) -> Optional[CartItem]:
        stmt = select(CartItem).options(selectinload(CartItem.product)).where(CartItem.id == item_id, CartItem.user_id == user_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
        
    async def get_items_by_ids(self, item_ids: List[int], user_id: int) -> List[CartItem]:
        stmt = select(CartItem).options(selectinload(CartItem.product)).where(CartItem.id.in_(item_ids), CartItem.user_id == user_id)
        return (await self.session.execute(stmt)).scalars().all()
