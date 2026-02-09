from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.web.routes import admin, shop, payme, click
from app.database.core import engine, Base, async_session_maker
from app.database.models import User
from app.utils.security import get_password_hash, verify_password
from app.utils.logger import logger

async def create_default_admin():
    """Создает суперадмина admin/admin123, если его нет"""
    async with async_session_maker() as session:
        try:
            # Проверяем, есть ли суперадмин по логину из конфига
            stmt = select(User).where(User.login == settings.SUPERADMIN_LOGIN)
            admin = (await session.execute(stmt)).scalar_one_or_none()
            
            # Хеш пароля из конфига
            pwd_hash = get_password_hash(settings.SUPERADMIN_PASSWORD)

            if not admin:
                logger.info(f"⚡ Суперадмин {settings.SUPERADMIN_LOGIN} не найден. Создаю...")
                
                new_admin = User(
                    telegram_id=None,
                    username="SuperAdmin",
                    login=settings.SUPERADMIN_LOGIN,
                    password_hash=pwd_hash,
                    role="superadmin",
                    phone="admin_contact"
                )
                session.add(new_admin)
                await session.commit()
                logger.info(f"✅ Суперадмин создан! Логин: {settings.SUPERADMIN_LOGIN}")
            else:
                # Обновляем пароль только если разрешено в настройках
                if not verify_password(settings.SUPERADMIN_PASSWORD, admin.password_hash):
                    if settings.SYNC_SUPERADMIN_PASSWORD:
                        admin.password_hash = pwd_hash
                        session.add(admin)
                        await session.commit()
                        logger.info(
                            f"🔄 Пароль суперадмина {settings.SUPERADMIN_LOGIN} обновлен из конфига."
                        )
                    else:
                        logger.warning(
                            "⚠️ Пароль суперадмина отличается от конфига, "
                            "но SYNC_SUPERADMIN_PASSWORD выключен — "
                            "автоматическое обновление не выполнено."
                        )
                else:
                    logger.info(f"✅ Суперадмин {settings.SUPERADMIN_LOGIN} уже существует и актуален.")
                
        except Exception as e:
            logger.error(f"Ошибка создания админа: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("🚀 Запуск приложения...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Инициализация категорий
    from init_categories import init_cats
    await init_cats()
    
    await create_default_admin()
    
    yield
    
    # Shutdown
    logger.info("🛑 Остановка приложения...")
    await engine.dispose()
    logger.info("Bye!")

app = FastAPI(title="Shop MiniApp", lifespan=lifespan)


# ВАЖНО: Подключаем сессии. 
app.add_middleware(
    SessionMiddleware, 
    secret_key=settings.SECRET_KEY, 
    max_age=86400 * 30, # Сессия живет 30 дней
    https_only=settings.SESSION_HTTPS_ONLY,    # Secure для HTTPS окружений
    same_site='lax'    # Разрешаем cross-site запросы (важно для WebApp)
)

# Подключаем статику
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# Подключаем папку медиа (для загруженных фото товаров)
app.mount("/media", StaticFiles(directory="media"), name="media")

templates = Jinja2Templates(directory="app/templates")

# Кастомный фильтр для UTC+5 (Узбекистан)
def format_datetime_uz(value, format="%d.%m.%Y %H:%M"):
    if value is None:
        return ""
    # Добавляем 5 часов для UTC+5
    local_dt = value + timedelta(hours=5)
    return local_dt.strftime(format)

templates.env.filters["datetime_uz"] = format_datetime_uz

# Подключаем роутеры
app.include_router(admin.router)
app.include_router(shop.router)
app.include_router(payme.router)
app.include_router(click.router)

@app.get("/")
async def index():
    # Корневой URL перенаправляет в админку (или можно на лендинг)
    return RedirectResponse(url="/shop")
