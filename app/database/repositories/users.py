from sqlalchemy import select, or_
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.models import User
from .base import BaseRepository

class UserRepository(BaseRepository[User]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, User)

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_id == telegram_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
        
    async def get_by_login(self, login: str) -> Optional[User]:
        stmt = select(User).where(User.login == login)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_admins(self) -> List[User]:
        stmt = select(User).where(User.role.in_(["manager", "superadmin"]))
        return (await self.session.execute(stmt)).scalars().all()
        
    async def get_with_lock(self, user_id: int) -> Optional[User]:
        stmt = select(User).where(User.id == user_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()
