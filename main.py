import asyncio
import logging
import uvicorn
from app.bot.loader import bot, dp
from app.web.app import app

async def start_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

async def start_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    # Запускаем параллельно
    await asyncio.gather(
        start_bot(),
        start_web()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass