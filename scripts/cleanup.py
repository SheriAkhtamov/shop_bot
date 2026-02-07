import asyncio
import sys
import os
import logging
from datetime import datetime, timedelta, timezone

# Add parent directory to path to allow importing app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.logger import logger

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.database.core import async_session_maker
from app.database.models import Order, PaymeTransaction, OrderItem

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# logger = logging.getLogger("cleanup")

async def cleanup_zombie_orders():
    """Фоновая задача: отменяет неоплаченные заказы старше 30 минут и возвращает сток"""
    logger.info("Starting zombie orders cleanup worker...")
    while True:
        try:
            async with async_session_maker() as session:
                threshold = datetime.utcnow() - timedelta(minutes=30)

                order_ids_stmt = select(Order.id).where(
                    Order.status == "new",
                    Order.created_at < threshold.replace(tzinfo=None),
                    ~Order.payme_transaction.has(PaymeTransaction.state == 1),
                )
                order_ids = (await session.execute(order_ids_stmt)).scalars().all()

                if order_ids:
                    logger.info(f"🧟 Найдено {len(order_ids)} зомби-заказов. Отменяем...")

                for order_id in order_ids:
                    async with session.begin():
                        stmt = (
                            select(Order)
                            .options(
                                selectinload(Order.items).selectinload(OrderItem.product),
                            )
                            .where(Order.id == order_id)
                            .with_for_update()
                        )
                        order = (await session.execute(stmt)).scalar_one_or_none()
                        if not order:
                            continue

                        if order.status != "new":
                            continue
                        if order.created_at >= threshold.replace(tzinfo=None):
                            continue

                        active_tx_stmt = select(PaymeTransaction.id).where(
                            PaymeTransaction.order_id == order.id,
                            PaymeTransaction.state == 1,
                        )
                        active_tx = (await session.execute(active_tx_stmt)).scalar_one_or_none()
                        if active_tx:
                            continue

                        order.status = "cancelled"
                        for item in order.items:
                            if item.product_id and item.product:
                                item.product.stock += item.quantity

        except Exception as e:
            logger.error(f"Ошибка в cleanup_zombie_orders: {e}")

        await asyncio.sleep(60) # Проверка каждую минуту

if __name__ == "__main__":
    try:
        asyncio.run(cleanup_zombie_orders())
    except KeyboardInterrupt:
        logger.info("Worker stopped")
