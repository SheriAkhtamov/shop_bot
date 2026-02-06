import asyncio
import time
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import Order, PaymeTransaction, User, Product, OrderItem
from app.config import settings
from app.bot.loader import bot

logger = logging.getLogger(__name__)

class PaymeErrors:
    INSUFFICIENT_PRIVILEGE = -32504
    JSON_PARSE_ERROR = -32700
    METHOD_NOT_FOUND = -32601
    INVALID_AMOUNT = -31001
    TRANSACTION_NOT_FOUND = -31003
    ORDER_NOT_FOUND = -31050
    ORDER_AVAILABLE = -31051
    CANT_CANCEL = -31007
    ALREADY_DONE = -31008

class PaymeException(Exception):
    def __init__(self, code: int, message: dict | str = "Error", data: str = None):
        self.code = code
        self.message = message
        self.data = data

class PaymeService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def check_perform_transaction(self, amount_tiyins: int, account: dict):
        order_id = account.get(settings.PAYME_ACCOUNT_FIELD)
        
        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
            raise PaymeException(PaymeErrors.ORDER_NOT_FOUND, {"ru": "Неверный ID заказа"})

        stmt = select(Order).where(Order.id == order_id)
        order = (await self.session.execute(stmt)).scalar_one_or_none()

        if not order:
            raise PaymeException(PaymeErrors.ORDER_NOT_FOUND, {"ru": "Заказ не найден"})

        if order.total_amount * 100 != amount_tiyins:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})

        if order.status != "new":
            raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ уже оплачен или отменен"})

        return {"allow": True}

    async def create_transaction(self, payme_id: str, time_ms: int, amount_tiyins: int, account: dict):
        order_id = account.get(settings.PAYME_ACCOUNT_FIELD)
        
        # Validate time (Payme guidelines: check if transaction is too old or from future)
        current_time = int(time.time() * 1000)
        
        # Check if transaction is in future (more than 1 minute tolerance for clock skew)
        if time_ms > current_time + 60000:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная дата транзакции (будущее время)"})

        # Check if transaction is too old (12 hours)
        if abs(current_time - time_ms) > 43200000: 
             raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная дата транзакции (таймаут)"})

        stmt_tx = select(PaymeTransaction).where(PaymeTransaction.payme_id == payme_id)
        transaction = (await self.session.execute(stmt_tx)).scalar_one_or_none()

        if transaction:
            if transaction.state != 1:
                raise PaymeException(PaymeErrors.CANT_CANCEL, {"ru": "Транзакция уже обрабатывается"})
            if transaction.order_id != int(order_id):
                 raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Неверный ID заказа"})
            
            return {
                "create_time": int(transaction.create_time.timestamp() * 1000),
                "transaction": str(transaction.id),
                "state": 1
            }

        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
             raise PaymeException(PaymeErrors.ORDER_NOT_FOUND, {"ru": "Неверный ID заказа"})

        stmt_order = select(Order).options(selectinload(Order.user), selectinload(Order.items).selectinload(OrderItem.product)).where(Order.id == order_id).with_for_update()
        order = (await self.session.execute(stmt_order)).scalar_one_or_none()

        if not order:
            raise PaymeException(PaymeErrors.ORDER_NOT_FOUND, {"ru": "Заказ не найден"})

        if order.total_amount * 100 != amount_tiyins:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})

        if order.status != "new":
            raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ уже оплачен или отменен"})

        if order.order_type == "debt_repayment":
            # Проверка на переплату
            if order.user and order.user.debt is not None:
                # amount_tiyins - сумма в тийинах
                # user.debt - долг в сумах
                debt_in_tiyins = order.user.debt * 100
                if amount_tiyins > debt_in_tiyins:
                     raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Сумма превышает текущий долг"})


        stmt_check = select(PaymeTransaction).where(
            PaymeTransaction.order_id == order_id,
            PaymeTransaction.state == 1
        )
        existing_active = (await self.session.execute(stmt_check)).scalar_one_or_none()
        if existing_active:
             raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Транзакция уже в процессе"})

        new_tx = PaymeTransaction(
            payme_id=payme_id,
            time=time_ms,
            amount=amount_tiyins,
            order_id=order_id,
            state=1
        )
        self.session.add(new_tx)
        await self.session.commit()

        return {
            "create_time": int(new_tx.create_time.timestamp() * 1000),
            "transaction": str(new_tx.id),
            "state": 1,
            "detail": {
                "receipt_type": 0,
                "items": [
                    {
                        "title": item.product_name,
                        "price": item.price_at_purchase * 100, # Tiyans
                        "count": item.quantity,
                        "code": item.product.ikpu if item.product and item.product.ikpu else "00702001001000001",
                        "units": 241092, # piece
                        "vat_percent": 0,
                        "package_code": item.product.package_code if item.product and item.product.package_code else "123456"
                    }
                    for item in order.items
                ]
            }
        }

    async def perform_transaction(self, payme_id: str):
        stmt = select(PaymeTransaction).where(PaymeTransaction.payme_id == payme_id).with_for_update()
        transaction = (await self.session.execute(stmt)).scalar_one_or_none()
        
        if not transaction:
            raise PaymeException(PaymeErrors.TRANSACTION_NOT_FOUND, {"ru": "Транзакция не найдена"})


        if transaction.state == 1:
            if transaction.create_time:
                # Check timeout (12 hours)
                t_create = transaction.create_time
                diff = (datetime.utcnow() - t_create).total_seconds()
                if diff > 43200:
                    transaction.state = -1
                    transaction.reason = 4
                    transaction.cancel_time = datetime.utcnow()
                    await self.session.commit()
                    raise PaymeException(PaymeErrors.ALREADY_DONE, {"ru": "Таймаут транзакции"})

            transaction.state = 2
            transaction.perform_time = datetime.utcnow()
            
            stmt_order = select(Order).options(selectinload(Order.user), selectinload(Order.items)).where(Order.id == transaction.order_id).with_for_update()
            order = (await self.session.execute(stmt_order)).scalar_one_or_none()
            
            if order:
                order.status = "paid"
                order.payment_method = "card"

                # Clear Cart for this user (products purchased in this order)
                # We iterate order items and remove corresponding cart items
                from sqlalchemy import delete
                from app.database.models import CartItem
                
                # Get product IDs from order
                product_ids = [item.product_id for item in order.items if item.product_id]
                
                if product_ids:
                    stmt_del = delete(CartItem).where(
                        CartItem.user_id == order.user_id,
                        CartItem.product_id.in_(product_ids)
                    )
                    await self.session.execute(stmt_del)

                 # ЛОГИКА ПОГАШЕНИЯ ДОЛГА
                if order.order_type == "debt_repayment":
                     order.status = "done" # Сразу завершен
                     
                     # Явная блокировка пользователя для обновления баланса
                     stmt_user = select(User).where(User.id == order.user_id).with_for_update()
                     user_locked = (await self.session.execute(stmt_user)).scalar_one_or_none()
                     
                     paid_amount = order.total_amount
                     
                     if user_locked:
                         if user_locked.debt < paid_amount:
                             user_locked.debt = 0
                         else:
                             user_locked.debt -= paid_amount
                     
                     # Уведомление
                     try:
                         msg = f"✅ <b>Долг погашен на {paid_amount} сум!</b>\nОстаток долга: {user_locked.debt if user_locked else 0} сум."
                         if order.user.telegram_id:
                             asyncio.create_task(bot.send_message(order.user.telegram_id, msg, parse_mode="HTML"))
                     except Exception:
                         logger.exception("Failed to send Payme debt repayment notification")
            
            await self.session.commit()
            
            return {
                "perform_time": int(transaction.perform_time.timestamp() * 1000),
                "transaction": str(transaction.id),
                "state": 2
            }

        if transaction.state == 2:
             return {
                "perform_time": int(transaction.perform_time.timestamp() * 1000),
                "transaction": str(transaction.id),
                "state": 2
            }

        raise PaymeException(PaymeErrors.CANT_CANCEL, {"ru": "Транзакция отменена"})

    async def cancel_transaction(self, payme_id: str, reason: int):
        stmt = select(PaymeTransaction).where(PaymeTransaction.payme_id == payme_id).with_for_update()
        transaction = (await self.session.execute(stmt)).scalar_one_or_none()
        
        if not transaction:
            raise PaymeException(PaymeErrors.TRANSACTION_NOT_FOUND, {"ru": "Транзакция не найдена"})

        # Идемпотентность: если уже отменена, возвращаем успех
        if transaction.state < 0:
             return {
                "cancel_time": int(transaction.cancel_time.timestamp() * 1000),
                "transaction": str(transaction.id),
                "state": transaction.state
            }

        # Отмена созданной (не оплаченной) транзакции
        if transaction.state == 1:
            transaction.state = -1
            transaction.reason = reason
            transaction.cancel_time = datetime.utcnow()
            await self.session.commit()
        
        # Отмена оплаченной транзакции (возврат средств)
        elif transaction.state == 2:
            transaction.state = -2
            transaction.reason = reason
            transaction.cancel_time = datetime.utcnow()
            
            stmt_order = select(Order).options(selectinload(Order.items)).where(Order.id == transaction.order_id).with_for_update()
            order = (await self.session.execute(stmt_order)).scalar_one_or_none()
            
            if order:
                order.status = "cancelled"
                
                # ВОЗВРАТ СРЕДСТВ / ТОВАРА
                if order.order_type == 'product':
                    # Возвращаем товары на склад
                    for item in order.items:
                        if item.product_id:
                             product_stmt = select(Product).where(Product.id == item.product_id).with_for_update()
                             product = (await self.session.execute(product_stmt)).scalar_one_or_none()
                             if product:
                                 product.stock += item.quantity
                
                elif order.order_type == 'debt_repayment':
                    # Возвращаем долг пользователю
                    stmt_user = select(User).where(User.id == order.user_id).with_for_update()
                    user = (await self.session.execute(stmt_user)).scalar_one_or_none()
                    if user:
                        user.debt += order.total_amount
            
            await self.session.commit()
            
        return {
            "cancel_time": int(transaction.cancel_time.timestamp() * 1000),
            "transaction": str(transaction.id),
            "state": transaction.state
        }

    async def check_transaction(self, payme_id: str):
        stmt = select(PaymeTransaction).where(PaymeTransaction.payme_id == payme_id)
        transaction = (await self.session.execute(stmt)).scalar_one_or_none()
        
        if not transaction:
             raise PaymeException(PaymeErrors.TRANSACTION_NOT_FOUND, {"ru": "Транзакция не найдена"})

        return {
            "create_time": int(transaction.create_time.timestamp() * 1000) if transaction.create_time else 0,
            "perform_time": int(transaction.perform_time.timestamp() * 1000) if transaction.perform_time else 0,
            "cancel_time": int(transaction.cancel_time.timestamp() * 1000) if transaction.cancel_time else 0,
            "transaction": str(transaction.id),
            "state": transaction.state,
            "reason": transaction.reason
        }

    async def get_statement(self, from_time: int, to_time: int):
        stmt = select(PaymeTransaction).where(
            PaymeTransaction.time >= from_time,
            PaymeTransaction.time <= to_time
        )
        transactions = (await self.session.execute(stmt)).scalars().all()
        
        return {
            "transactions": [
                {
                    "id": tx.payme_id,
                    "time": tx.time,
                    "amount": tx.amount,
                    "account": {settings.PAYME_ACCOUNT_FIELD: str(tx.order_id)},
                    "create_time": int(tx.create_time.timestamp() * 1000),
                    "perform_time": int(tx.perform_time.timestamp() * 1000) if tx.perform_time else 0,
                    "cancel_time": int(tx.cancel_time.timestamp() * 1000) if tx.cancel_time else 0,
                    "transaction": str(tx.id),
                    "state": tx.state,
                    "reason": tx.reason
                }
                for tx in transactions
            ]
        }
