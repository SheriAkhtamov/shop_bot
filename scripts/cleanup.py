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
from app.services.order_service import OrderService

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# logger = logging.getLogger("cleanup")

async def cleanup_zombie_orders():
    """Фоновая задача: отменяет неоплаченные заказы старше 30 минут и возвращает сток"""
    logger.info("Starting zombie orders cleanup worker...")
    while True:
        try:
            async with async_session_maker() as session:
                threshold_order = datetime.utcnow() - timedelta(minutes=30)
                threshold_tx = datetime.utcnow() - timedelta(minutes=30)

                order_ids_stmt = select(Order.id).where(
                    Order.status == "new",
                    (
                        (Order.created_at < threshold_order.replace(tzinfo=None))
                        | Order.payme_transaction.has(PaymeTransaction.state == 1)
                    ),
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
                                selectinload(Order.payme_transaction),
                            )
                            .where(Order.id == order_id)
                            .with_for_update()
                        )
                        order = (await session.execute(stmt)).scalar_one_or_none()
                        if not order:
                            continue

                        if order.status != "new":
                            continue

                        active_tx = order.payme_transaction
                        if active_tx and active_tx.state == 1:
                            if active_tx.create_time >= threshold_tx.replace(tzinfo=None):
                                continue

                            active_tx.state = -1
                            active_tx.reason = 4
                            active_tx.cancel_time = datetime.utcnow()
                            await OrderService.cancel_order(session, order.id, commit=False)
                            continue

                        if order.created_at >= threshold_order.replace(tzinfo=None):
                            continue

                        await OrderService.cancel_order(session, order.id, commit=False)

        except Exception as e:
            logger.error(f"Ошибка в cleanup_zombie_orders: {e}")

        await asyncio.sleep(60) # Проверка каждую минуту

if __name__ == "__main__":
    try:
        asyncio.run(cleanup_zombie_orders())
    except KeyboardInterrupt:
        logger.info("Worker stopped")
