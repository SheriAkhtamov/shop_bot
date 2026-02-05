from aiogram import Bot, Dispatcher
from app.config import settings
from app.database.core import async_session_maker
from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.handlers import start

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher()

# Подключаем Middleware
dp.update.middleware(DbSessionMiddleware(session_pool=async_session_maker))

# Подключаем роутеры
dp.include_router(start.router)