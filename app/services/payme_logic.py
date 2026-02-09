import asyncio
import time
from datetime import datetime, timedelta
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import OperationalError
from app.database.models import Order, PaymeTransaction, User, Product, OrderItem
from app.config import settings
from app.bot.loader import bot
from app.services.order_service import OrderService
from app.utils.money import normalize_amount
from app.utils.logger import logger

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
    LOCK_TIMEOUT = "5s"
    DEFAULT_TIMEOUT_MINUTES = 720

    def _transaction_timeout_minutes(self) -> int:
        return getattr(settings, "ORDER_PAYMENT_TIMEOUT_MINUTES", self.DEFAULT_TIMEOUT_MINUTES)

    def _transaction_timeout_ms(self) -> int:
        return self._transaction_timeout_minutes() * 60 * 1000

    def _transaction_timeout_seconds(self) -> int:
        return self._transaction_timeout_minutes() * 60

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _set_lock_timeout(self) -> None:
        await self.session.execute(
            text("SET LOCAL lock_timeout = :timeout"),
            {"timeout": self.LOCK_TIMEOUT},
        )

    def _is_lock_error(self, error: OperationalError) -> bool:
        orig = getattr(error, "orig", None)
        if orig and orig.__class__.__name__ == "LockNotAvailable":
            return True
        message = str(error).lower()
        return "lock timeout" in message or "lock not available" in message or "could not obtain lock" in message

    async def _raise_lock_error(self) -> None:
        await self.session.rollback()
        raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ занят, попробуйте позже"})

    async def check_perform_transaction(self, amount_tiyins: int, account: dict):
        try:
            amount_tiyins = normalize_amount(amount_tiyins)
        except ValueError:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})
        order_id = account.get(settings.PAYME_ACCOUNT_FIELD)
        
        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
            raise PaymeException(
                PaymeErrors.ORDER_NOT_FOUND,
                {"ru": "Неверный ID заказа"},
                data=settings.PAYME_ACCOUNT_FIELD,
            )

        stmt = select(Order).where(Order.id == order_id)
        order = (await self.session.execute(stmt)).scalar_one_or_none()

        if not order:
            raise PaymeException(
                PaymeErrors.ORDER_NOT_FOUND,
                {"ru": "Заказ не найден"},
                data=settings.PAYME_ACCOUNT_FIELD,
            )

        if order.order_type == "debt_repayment" and order.payment_method != "card":
            raise PaymeException(
                PaymeErrors.ORDER_AVAILABLE,
                {"ru": "Погашение долга доступно только через Payme"},
            )

        if order.payment_method != "card":
            raise PaymeException(
                PaymeErrors.ORDER_AVAILABLE,
                {"ru": "Заказ не доступен для оплаты через Payme"},
            )

        if await OrderService.cancel_expired_online_order(self.session, order):
            raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ просрочен и отменен"})

        if order.total_amount * 100 != amount_tiyins:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})

        if order.status != "new":
            raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ уже оплачен или отменен"})

        return {"allow": True}

    async def create_transaction(self, payme_id: str, time_ms: int, amount_tiyins: int, account: dict):
        try:
            amount_tiyins = normalize_amount(amount_tiyins)
        except ValueError:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})
        order_id = account.get(settings.PAYME_ACCOUNT_FIELD)
        
        # Validate time (Payme guidelines: check if transaction is too old or from future)
        current_time = int(time.time() * 1000)
        
        # Check if transaction is in future (more than 1 minute tolerance for clock skew)
        if time_ms > current_time + 60000:
            raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная дата транзакции (будущее время)"})

        # Check if transaction is too old (configured timeout)
        if abs(current_time - time_ms) > self._transaction_timeout_ms(): 
             raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная дата транзакции (таймаут)"})

        stmt_tx = select(PaymeTransaction).where(PaymeTransaction.payme_id == payme_id)
        transaction = (await self.session.execute(stmt_tx)).scalar_one_or_none()

        if transaction:
            # Идемпотентность Payme: повторный вызов возвращает текущее состояние транзакции.
            if transaction.amount != amount_tiyins:
                raise PaymeException(PaymeErrors.INVALID_AMOUNT, {"ru": "Неверная сумма"})
            try:
                order_id_int = int(order_id)
            except (ValueError, TypeError):
                raise PaymeException(
                    PaymeErrors.ORDER_NOT_FOUND,
                    {"ru": "Неверный ID заказа"},
                    data=settings.PAYME_ACCOUNT_FIELD,
                )
            if transaction.order_id != order_id_int:
                raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Неверный ID заказа"})
            if transaction.state != 1:
                return {
                    "create_time": int(transaction.create_time.timestamp() * 1000),
                    "perform_time": int(transaction.perform_time.timestamp() * 1000)
                    if transaction.perform_time
                    else 0,
                    "cancel_time": int(transaction.cancel_time.timestamp() * 1000)
                    if transaction.cancel_time
                    else 0,
                    "transaction": str(transaction.id),
                    "state": transaction.state,
                }
            
            return {
                "create_time": int(transaction.create_time.timestamp() * 1000),
                "transaction": str(transaction.id),
                "state": 1
            }

        try:
            order_id = int(order_id)
        except (ValueError, TypeError):
             raise PaymeException(
                 PaymeErrors.ORDER_NOT_FOUND,
                 {"ru": "Неверный ID заказа"},
                 data=settings.PAYME_ACCOUNT_FIELD,
             )

        try:
            await self._set_lock_timeout()
            stmt_order = (
                select(Order)
                .options(
                    selectinload(Order.user),
                    selectinload(Order.items),
                    selectinload(Order.items).selectinload(OrderItem.product),
                )
                .where(Order.id == order_id)
                .with_for_update()
            )
            order = (await self.session.execute(stmt_order)).scalar_one_or_none()
        except OperationalError as error:
            if self._is_lock_error(error):
                await self._raise_lock_error()
            raise

        if not order:
            raise PaymeException(
                PaymeErrors.ORDER_NOT_FOUND,
                {"ru": "Заказ не найден"},
                data=settings.PAYME_ACCOUNT_FIELD,
            )

        if order.order_type == "debt_repayment" and order.payment_method != "card":
            raise PaymeException(
                PaymeErrors.ORDER_AVAILABLE,
                {"ru": "Погашение долга доступно только через Payme"},
            )

        if order.payment_method != "card":
            raise PaymeException(
                PaymeErrors.ORDER_AVAILABLE,
                {"ru": "Заказ не доступен для оплаты через Payme"},
            )

        if await OrderService.cancel_expired_online_order(self.session, order):
            raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ просрочен и отменен"})

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
                     await OrderService.cancel_order(self.session, order.id, commit=False)
                     await self.session.commit()
                     raise PaymeException(
                         PaymeErrors.INVALID_AMOUNT,
                         {"ru": "Сумма превышает текущий долг. Заказ отменен"},
                     )
        elif order.order_type == "product":
            if not order.items:
                raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Order not ready"})


        stmt_check = select(PaymeTransaction).where(
            PaymeTransaction.order_id == order_id,
            PaymeTransaction.state == 1
        )
        existing_active = (await self.session.execute(stmt_check)).scalar_one_or_none()
        if existing_active:
            existing_active.state = -1
            existing_active.reason = 4
            existing_active.cancel_time = datetime.utcnow()
            await self.session.flush()

        new_tx = PaymeTransaction(
            payme_id=payme_id,
            time=time_ms,
            amount=amount_tiyins,
            order_id=order_id,
            state=1
        )
        new_tx.order = order
        self.session.add(new_tx)
        await self.session.commit()

        if order.order_type == "debt_repayment" and not order.items:
            receipt_items = [
                {
                    "title": "Погашение долга",
                    "price": order.total_amount * 100,  # Tiyans
                    "count": 1,
                    "code": "00702001001000001",
                    "units": 241092,  # piece
                    "vat_percent": 0,
                    "package_code": settings.DEFAULT_PACKAGE_CODE,
                }
            ]
        else:
            receipt_items = [
                {
                    "title": item.product_name,
                    "price": item.price_at_purchase * 100, # Tiyans
                    "count": item.quantity,
                    "code": item.product.ikpu if item.product and item.product.ikpu else "00702001001000001",
                    "units": 241092, # piece
                    "vat_percent": 0,
                    "package_code": item.product.package_code if item.product and item.product.package_code else settings.DEFAULT_PACKAGE_CODE
                }
                for item in order.items
            ]

        return {
            "create_time": int(new_tx.create_time.timestamp() * 1000),
            "transaction": str(new_tx.id),
            "state": 1,
            "detail": {
                "receipt_type": 0,
                "items": receipt_items
            }
        }

    async def perform_transaction(self, payme_id: str):
        try:
            await self._set_lock_timeout()
            stmt = (
                select(PaymeTransaction)
                .where(PaymeTransaction.payme_id == payme_id)
                .with_for_update()
            )
            transaction = (await self.session.execute(stmt)).scalar_one_or_none()
        except OperationalError as error:
            if self._is_lock_error(error):
                await self._raise_lock_error()
            raise
        
        if not transaction:
            raise PaymeException(PaymeErrors.TRANSACTION_NOT_FOUND, {"ru": "Транзакция не найдена"})


        if transaction.state == 1:
            if transaction.create_time:
                # Check timeout (configured)
                t_create = transaction.create_time
                diff = (datetime.utcnow() - t_create).total_seconds()
                if diff > self._transaction_timeout_seconds():
                    transaction.state = -1
                    transaction.reason = 4
                    transaction.cancel_time = datetime.utcnow()
                    await self.session.commit()
                    raise PaymeException(PaymeErrors.ALREADY_DONE, {"ru": "Таймаут транзакции"})

            try:
                await self._set_lock_timeout()
                stmt_order = (
                    select(Order)
                    .options(
                        selectinload(Order.user),
                        selectinload(Order.items),
                        selectinload(Order.items).selectinload(OrderItem.product),
                    )
                    .where(Order.id == transaction.order_id)
                    .with_for_update()
                )
                order = (await self.session.execute(stmt_order)).scalar_one_or_none()
            except OperationalError as error:
                if self._is_lock_error(error):
                    await self._raise_lock_error()
                raise
            
            if not order:
                raise PaymeException(
                    PaymeErrors.ORDER_NOT_FOUND,
                    {"ru": "Заказ не найден"},
                    data=settings.PAYME_ACCOUNT_FIELD,
                )

            if order.payment_method != "card":
                raise PaymeException(
                    PaymeErrors.ORDER_AVAILABLE,
                    {"ru": "Заказ не доступен для оплаты через Payme"},
                )

            if await OrderService.cancel_expired_online_order(self.session, order):
                raise PaymeException(PaymeErrors.ORDER_AVAILABLE, {"ru": "Заказ просрочен и отменен"})

            allowed_statuses = {"new"}
            if order.status not in allowed_statuses:
                raise PaymeException(
                    PaymeErrors.ORDER_AVAILABLE,
                    {"ru": f"Заказ не доступен для оплаты в статусе {order.status}"},
                )

            transaction.state = 2
            transaction.perform_time = datetime.utcnow()

            if order.status in {"paid", "done"}:
                await self.session.commit()
                return {
                    "perform_time": int(transaction.perform_time.timestamp() * 1000) if transaction.perform_time else 0,
                    "transaction": str(transaction.id),
                    "state": transaction.state
                }

            user_locked = None
            if order.order_type == "debt_repayment":
                try:
                    await self._set_lock_timeout()
                    stmt_user = select(User).where(User.id == order.user_id).with_for_update()
                    user_locked = (await self.session.execute(stmt_user)).scalar_one_or_none()
                except OperationalError as error:
                    if self._is_lock_error(error):
                        await self._raise_lock_error()
                    raise
                current_debt = user_locked.debt if user_locked and user_locked.debt is not None else 0
                if order.total_amount > current_debt:
                    await OrderService.cancel_order(self.session, order.id, commit=False)
                    await self.session.commit()
                    raise PaymeException(
                        PaymeErrors.INVALID_AMOUNT,
                        {"ru": "Сумма превышает текущий долг. Заказ отменен"},
                    )

            order.status = "paid"
            order.payment_method = "card"

            # Reduce cart quantities only for items included in this order.
            # This avoids wiping out newer cart additions made after order creation.
            from collections import defaultdict
            from app.database.models import CartItem

            ordered_quantities = defaultdict(int)
            for item in order.items:
                if item.product_id:
                    ordered_quantities[item.product_id] += item.quantity

            if ordered_quantities:
                try:
                    await self._set_lock_timeout()
                    cart_stmt = (
                        select(CartItem)
                        .where(
                            CartItem.user_id == order.user_id,
                            CartItem.product_id.in_(ordered_quantities.keys()),
                        )
                        .order_by(CartItem.id)
                        .with_for_update()
                    )
                    cart_items = (await self.session.execute(cart_stmt)).scalars().all()
                except OperationalError as error:
                    if self._is_lock_error(error):
                        await self._raise_lock_error()
                    raise
                for cart_item in cart_items:
                    remaining = ordered_quantities.get(cart_item.product_id, 0)
                    if remaining <= 0:
                        continue
                    if cart_item.quantity > remaining:
                        cart_item.quantity -= remaining
                        ordered_quantities[cart_item.product_id] = 0
                    else:
                        ordered_quantities[cart_item.product_id] = remaining - cart_item.quantity
                        await self.session.delete(cart_item)

            # ЛОГИКА ПОГАШЕНИЯ ДОЛГА
            if order.order_type == "debt_repayment":
                order.status = "done"  # Сразу завершен

                paid_amount = order.total_amount

                if user_locked:
                    if user_locked.debt < paid_amount:
                        user_locked.debt = 0
                    else:
                        user_locked.debt -= paid_amount

                # Уведомление
                try:
                    msg = (
                        f"✅ <b>Долг погашен на {paid_amount} сум!</b>\n"
                        f"Остаток долга: {user_locked.debt if user_locked else 0} сум."
                    )
                    if order.user.telegram_id:
                        asyncio.create_task(
                            bot.send_message(order.user.telegram_id, msg, parse_mode="HTML")
                        )
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

        if transaction.state < 0:
            raise PaymeException(
                PaymeErrors.ALREADY_DONE,
                {"ru": "Транзакция отменена или завершена"},
            )

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

        # Запрет отмены подтвержденной транзакции без возврата
        if transaction.state == 2:
            raise PaymeException(
                PaymeErrors.CANT_CANCEL,
                {"ru": "Отмена оплаченной транзакции возможна только после подтвержденного возврата Payme"},
            )

        # Отмена созданной (не оплаченной) транзакции
        if transaction.state == 1:
            transaction.state = -1
            transaction.reason = reason
            transaction.cancel_time = datetime.utcnow()
            await OrderService.cancel_order(self.session, transaction.order_id, commit=False)
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
