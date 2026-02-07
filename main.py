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

async def run_with_restart(name, coro_factory, delay=5):
    while True:
        try:
            logging.info("%s started", name)
            await coro_factory()
            logging.warning("%s stopped; restarting in %s seconds", name, delay)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("%s crashed; restarting in %s seconds", name, delay)
        await asyncio.sleep(delay)

async def main():
    logging.basicConfig(level=logging.INFO)
    # Запускаем параллельно с перезапуском
    tasks = (
        asyncio.create_task(run_with_restart("bot", start_bot)),
        asyncio.create_task(run_with_restart("web", start_web)),
    )
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
