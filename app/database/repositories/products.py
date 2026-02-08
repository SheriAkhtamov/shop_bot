from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import Product
from .base import BaseRepository

class ProductRepository(BaseRepository[Product]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, Product)

    async def get_active(self, limit: int = 50) -> List[Product]:
        stmt = select(Product).where(Product.is_active == True).limit(limit)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_by_category(self, category_id: int) -> List[Product]:
         stmt = select(Product).where(Product.is_active == True, Product.category_id == category_id)
         return (await self.session.execute(stmt)).scalars().all()

    async def search(self, query: str) -> List[Product]:
        # Escape special characters for ILIKE pattern
        # The backslash itself matches a backslash, so we need to escape it first.
        safe_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        
        stmt = select(Product).where(
            Product.is_active == True, 
            (Product.name_ru.ilike(f"%{safe_query}%", escape="\\")) 
            | (Product.name_uz.ilike(f"%{safe_query}%", escape="\\"))
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_with_lock(self, product_id: int) -> Optional[Product]:
        stmt = select(Product).where(Product.id == product_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()
