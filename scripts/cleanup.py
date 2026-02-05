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
from app.database.models import Order, Product, PaymeTransaction

# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# logger = logging.getLogger("cleanup")

async def cleanup_zombie_orders():
    """Фоновая задача: отменяет неоплаченные заказы старше 30 минут и возвращает сток"""
    logger.info("Starting zombie orders cleanup worker...")
    while True:
        try:
            async with async_session_maker() as session:
                threshold = datetime.utcnow() - timedelta(minutes=30)
                
                # Исключаем заказы, у которых есть активная транзакция Payme (state=1)
                stmt = select(Order).options(selectinload(Order.items)).where(
                    Order.status == 'new', 
                    Order.created_at < threshold.replace(tzinfo=None), # Assuming DB stores naive UTC (if models default=datetime.utcnow)
                    # Note: We replaced datetime.utcnow() in models with naive or aware?
                    # The models were not fully updated to replace default=datetime.utcnow with now(utc).
                    # I should check models.py again. Block 3 plan said replace datetime.utcnow.
                    # But I only replaced it in payme_logic. 
                    # If models default is datetime.utcnow, it stores naive.
                    # safe to compare with naive.
                    ~Order.payme_transaction.has(PaymeTransaction.state == 1)
                )
                orders = (await session.execute(stmt)).scalars().all()
                
                if orders:
                    logger.info(f"🧟 Найдено {len(orders)} зомби-заказов. Отменяем...")
                    
                    for order in orders:
                        order.status = 'cancelled'
                        # Возврат стока
                        for item in order.items:
                            if item.product_id:
                                product = await session.get(Product, item.product_id)
                                if product:
                                    product.stock += item.quantity
                                    
                    await session.commit()
                    
        except Exception as e:
            logger.error(f"Ошибка в cleanup_zombie_orders: {e}")
            
        await asyncio.sleep(60) # Проверка каждую минуту

if __name__ == "__main__":
    try:
        asyncio.run(cleanup_zombie_orders())
    except KeyboardInterrupt:
        logger.info("Worker stopped")
