import asyncio
from app.database.core import Base, async_session_maker, engine
from app.database.models import User
from app.utils.security import get_password_hash

async def create_superadmin():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        print("Создание Супер-админа...")
        
        login = input("Введите логин: ")
        password = input("Введите пароль: ")
        
        # Хешируем пароль
        pwd_hash = get_password_hash(password)
        
        # Создаем юзера (без telegram_id, так как это веб-админ)
        # Внимание: telegram_id уникален, поэтому для чисто веб-юзера можно ставить заглушку или Null (если поле позволяет), 
        # но у нас в модели telegram_id обязателен и BigInteger. 
        # Давайте сделаем фиктивный telegram_id для админа, например 0 или 1.
        
        admin = User(
            telegram_id=None, # Заглушка
            username="SuperAdmin",
            login=login,
            password_hash=pwd_hash,
            role="superadmin",
            phone="admin"
        )
        
        try:
            session.add(admin)
            await session.commit()
            print(f"✅ Супер-админ {login} успешно создан!")
        except Exception as e:
            print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(create_superadmin())
