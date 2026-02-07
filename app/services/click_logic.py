import hashlib
import time
import aiohttp
import asyncio
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import Order, ClickTransaction, User, CartItem
from app.config import settings
from app.bot.loader import bot
from app.services.order_service import OrderService
from app.utils.money import normalize_amount
import logging

logger = logging.getLogger(__name__)

class ClickErrors:
    SUCCESS = 0
    SIGN_CHECK_FAILED = -1
    INCORRECT_AMOUNT = -2
    ACTION_NOT_FOUND = -3
    ALREADY_PAID = -4
    USER_DOES_NOT_EXIST = -5
    TRANSACTION_DOES_NOT_EXIST = -6
    FAILED_TO_UPDATE_USER = -7
    ERROR_IN_REQUEST = -8
    TRANSACTION_CANCELLED = -9

class ClickService:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _parse_amount(raw_amount):
        if raw_amount is None:
            raise ValueError("Amount is missing")
        normalized = "".join(str(raw_amount).split()).replace(",", ".")
        amount = Decimal(normalized)
        if amount != amount.to_integral_value():
            raise ValueError("Amount must be integer")
        return amount

    def check_sign(self, **kwargs):
        """Проверка цифровой подписи (MD5)"""
        click_trans_id = kwargs.get('click_trans_id')
        service_id = kwargs.get('service_id')
        merchant_trans_id = kwargs.get('merchant_trans_id')
        amount = kwargs.get('amount')
        action = kwargs.get('action')
        sign_time = kwargs.get('sign_time')
        sign_string = kwargs.get('sign_string')

        secret_key = settings.CLICK_SECRET_KEY
        
        # Формула из документации:
        # md5( click_trans_id + service_id + SECRET_KEY + merchant_trans_id + amount + action + sign_time )
        text = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{amount}{action}{sign_time}"
        my_sign = hashlib.md5(text.encode('utf-8')).hexdigest()

        return my_sign == sign_string

    async def prepare(self, data: dict):
        """Этап 1: Проверка возможности оплаты"""
        merchant_trans_id = data.get('merchant_trans_id')
        try:
            amount = self._parse_amount(data.get('amount'))
        except (TypeError, ValueError, ArithmeticError):
            return {"error": ClickErrors.INCORRECT_AMOUNT, "error_note": "Incorrect Amount"}

        # 1. Проверка action (должен быть 0 для prepare)
        try:
            action = int(data.get('action'))
        except (TypeError, ValueError):
            return {"error": ClickErrors.ERROR_IN_REQUEST, "error_note": "Invalid action"}

        if action != 0:
            return {"error": ClickErrors.ACTION_NOT_FOUND, "error_note": "Action not found"}

        # 1. Проверка подписи
        if not self.check_sign(**data):
            return {"error": ClickErrors.SIGN_CHECK_FAILED, "error_note": "Sign check failed"}

        # 2. Ищем заказ
        try:
            order_id = int(merchant_trans_id)
        except (TypeError, ValueError):
            return {"error": ClickErrors.USER_DOES_NOT_EXIST, "error_note": "Invalid Order ID"}

        stmt = select(Order).where(Order.id == order_id)
        order = (await self.session.execute(stmt)).scalar_one_or_none()

        if not order:
            return {"error": ClickErrors.USER_DOES_NOT_EXIST, "error_note": "Order not found"}

        if await OrderService.cancel_expired_online_order(self.session, order):
            return {"error": ClickErrors.TRANSACTION_CANCELLED, "error_note": "Order expired"}

        # 3. Проверка суммы
        order_total = Decimal(order.total_amount)
        if amount != order_total:
            return {"error": ClickErrors.INCORRECT_AMOUNT, "error_note": "Incorrect Amount"}

        # 4. Проверка статуса (если уже оплачен)
        if order.status in ['paid', 'done']:
             return {"error": ClickErrors.ALREADY_PAID, "error_note": "Already paid"}
        
        if order.status == 'cancelled':
             return {"error": ClickErrors.TRANSACTION_CANCELLED, "error_note": "Order cancelled"}

        # Всё ок
        return {
            "click_trans_id": data['click_trans_id'],
            "merchant_trans_id": merchant_trans_id,
            "merchant_prepare_id": merchant_trans_id, # Обычно используется ID транзакции, но у нас ID заказа
            "error": ClickErrors.SUCCESS,
            "error_note": "Success"
        }

    async def send_fiscal_data(self, payment_id: int, order: Order):
        """
        Отправка фискальных данных в Click (см. файл Фискализация данных.pdf)
        """
        url = "https://api.click.uz/v2/merchant/payment/ofd_data/submit_items"
        
        # Формируем список товаров
        items_list = []
        for item in order.items:
            items_list.append({
                "spic": item.product.ikpu if item.product and item.product.ikpu else "00702001001000001", # ИКПУ
                "title": item.product_name,
                "package_code": item.product.package_code if item.product and item.product.package_code else "123456",
                "price": int(item.price_at_purchase) * 100, # В документации Click сумма items не всегда в тийинах, но обычно API работают с минимальными единицами. Проверим доку: "price: цена...". В Click обычно сумы. НО! Payme в тийинах. 
                # ВАЖНО: В PDF Click написано "price: * uint64". И пример 505000. Это похоже на сумы или тийины? 
                # В примере "amount": 1000. В "submit_items" price 505000. Скорее всего в сумах. 
                # СТОП. В PDF написано "price... сумма... в тийинах" (стр 1 Item description).
                # ЗНАЧИТ УМНОЖАЕМ НА 100.
                "amount": item.quantity, # Количество
                "units": 241092, # Штуки (код единицы)
                "vat_percent": 0 # НДС
            })
            
        # Для услуг (погашение долга)
        if order.order_type == 'debt_repayment':
             items_list.append({
                "spic": "00702001001000001",
                "title": "Погашение долга",
                "package_code": "123456",
                "price": int(order.total_amount) * 100,
                "amount": 1,
                "units": 241092,
                "vat_percent": 0
            })

        # Формируем тело запроса
        timestamp = int(time.time())
        digest = hashlib.sha1(f"{timestamp}{settings.CLICK_SECRET_KEY}".encode('utf-8')).hexdigest()
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Auth": f"{settings.CLICK_MERCHANT_USER_ID}:{digest}:{timestamp}"
        }
        
        payload = {
            "service_id": int(settings.CLICK_SERVICE_ID),
            "payment_id": payment_id, # ID платежа в системе CLICK (не наш!)
            "items": items_list,
            "received_ecash": int(order.total_amount) * 100, # Сумма электронными (текущая оплата)
            "received_cash": 0,
            "received_card": 0
        }

        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.post(url, headers=headers, json=payload) as resp:
                    resp_data = await resp.json()
                    if resp.status != 200:
                        logger.error(f"Click Fiscal Error: {resp_data}")
                    else:
                        logger.info(f"Click Fiscal Success: {resp_data}")
        except Exception as e:
            logger.error(f"Click Fiscal Request Failed: {e}")

    async def complete(self, data: dict):
        """Этап 2: Проведение оплаты"""
        merchant_trans_id = data.get('merchant_trans_id')
        try:
            amount = self._parse_amount(data.get('amount'))
        except (TypeError, ValueError, ArithmeticError):
            return {"error": ClickErrors.INCORRECT_AMOUNT, "error_note": "Incorrect Amount"}
        try:
            click_trans_id = int(data.get('click_trans_id'))
        except (TypeError, ValueError):
            return {"error": ClickErrors.ERROR_IN_REQUEST, "error_note": "Invalid click_trans_id"}
        
        # 1. Проверка action (должен быть 1 для complete)
        try:
            action = int(data.get('action'))
        except (TypeError, ValueError):
            return {"error": ClickErrors.ERROR_IN_REQUEST, "error_note": "Invalid action"}

        if action != 1:
            return {"error": ClickErrors.ACTION_NOT_FOUND, "error_note": "Action not found"}

        # 2. Проверка подписи
        if not self.check_sign(**data):
            return {"error": ClickErrors.SIGN_CHECK_FAILED, "error_note": "Sign check failed"}
        
        # 3. Ищем заказ
        try:
            order_id = int(merchant_trans_id)
        except (TypeError, ValueError):
            return {"error": ClickErrors.USER_DOES_NOT_EXIST, "error_note": "Invalid Order ID"}

        stmt = select(Order).options(selectinload(Order.user), selectinload(Order.items)).where(Order.id == order_id).with_for_update()
        order = (await self.session.execute(stmt)).scalar_one_or_none()

        if not order:
            return {"error": ClickErrors.USER_DOES_NOT_EXIST, "error_note": "Order not found"}

        if await OrderService.cancel_expired_online_order(self.session, order):
            return {"error": ClickErrors.TRANSACTION_CANCELLED, "error_note": "Order expired"}

        # 4. Проверка на отмену (если запрос action=1, но error < 0, значит Click отменяет платеж)
        try:
            error_code = int(data.get('error', 0))
        except (TypeError, ValueError):
            return {"error": ClickErrors.ERROR_IN_REQUEST, "error_note": "Invalid error code"}

        if error_code < 0:
            if order.status == "cancelled":
                return {
                    "click_trans_id": click_trans_id,
                    "merchant_trans_id": merchant_trans_id,
                    "error": ClickErrors.SUCCESS,
                    "error_note": "Transaction already cancelled",
                }

            # Явно обрабатываем отмену для оплаченных/завершенных заказов.
            await OrderService.cancel_order(self.session, order.id)
            await self.session.commit()

            return {
                "click_trans_id": click_trans_id,
                "merchant_trans_id": merchant_trans_id,
                "error": ClickErrors.SUCCESS,
                "error_note": "Transaction cancelled",
            }

        if order.status in ("paid", "done"):
            return {"error": ClickErrors.ALREADY_PAID, "error_note": "Order already paid"}

        if order.status == "cancelled":
            return {"error": ClickErrors.TRANSACTION_CANCELLED, "error_note": "Transaction cancelled"}
        
        # 3. Идемпотентность (если Click прислал повторный запрос на уже проведенную оплату)
        # Проверяем, есть ли уже успешная транзакция с таким click_trans_id
        tx_stmt = select(ClickTransaction).where(ClickTransaction.click_trans_id == click_trans_id, ClickTransaction.status == 'confirmed')
        existing_tx = (await self.session.execute(tx_stmt)).scalar_one_or_none()
        
        if existing_tx:
             return {
                "click_trans_id": click_trans_id,
                "merchant_trans_id": merchant_trans_id,
                "merchant_confirm_id": order.id,
                "error": ClickErrors.SUCCESS,
                "error_note": "Already confirmed"
            }

        # 5. Проводим оплату
        order_total = Decimal(order.total_amount)
        if amount != order_total:
            return {"error": ClickErrors.INCORRECT_AMOUNT, "error_note": "Incorrect Amount"}

        user_locked = None
        if order.order_type == 'debt_repayment':
            user_stmt = select(User).where(User.id == order.user_id).with_for_update()
            user_locked = (await self.session.execute(user_stmt)).scalar_one_or_none()
            current_debt = user_locked.debt if user_locked and user_locked.debt is not None else 0
            if Decimal(order.total_amount) > Decimal(current_debt):
                return {
                    "error": ClickErrors.INCORRECT_AMOUNT,
                    "error_note": "Amount exceeds current debt",
                }

        if order.status == 'new':
            order.status = 'paid'
            order.payment_method = 'click'
            
            # Уменьшаем корзину только по позициям, вошедшим в заказ.
            # Это защищает новые товары/количества, добавленные после создания заказа.
            from collections import defaultdict

            ordered_quantities = defaultdict(int)
            for item in order.items:
                if item.product_id:
                    ordered_quantities[item.product_id] += item.quantity

            if ordered_quantities:
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
            
            # Погашение долга
            if order.order_type == 'debt_repayment':
                order.status = 'done'
                if user_locked:
                    if user_locked.debt < order.total_amount:
                        user_locked.debt = 0 # Безопасное списание
                    else:
                        user_locked.debt -= order.total_amount
            
            # Записываем транзакцию
            new_tx = ClickTransaction(
                click_trans_id=click_trans_id,
                service_id=int(data.get('service_id')),
                click_paydoc_id=int(data.get('click_paydoc_id')),
                merchant_trans_id=merchant_trans_id,
                amount=amount,
                action=1,
                error=0,
                sign_time=data.get('sign_time'),
                sign_string=data.get('sign_string'),
                status='confirmed'
            )
            self.session.add(new_tx)
            await self.session.commit()
            
            # Отправляем чек в налоговую через Click
            # click_trans_id - это ID платежа в системе Click
            try:
                 # Запускаем в фоне, чтобы не тормозить ответ
                asyncio.create_task(self.send_fiscal_data(click_trans_id, order))
            except Exception as e:
                logger.error(f"Failed to start fiscal task: {e}")
            
            # Уведомление
            try:
                import asyncio
                msg = f"✅ <b>Заказ #{order.id} оплачен через Click!</b>\nСумма: {order.total_amount} сум"
                if order.user.telegram_id:
                    asyncio.create_task(bot.send_message(order.user.telegram_id, msg, parse_mode="HTML"))
            except Exception:
                logger.exception("Failed to send Click payment notification")

        return {
            "click_trans_id": click_trans_id,
            "merchant_trans_id": merchant_trans_id,
            "merchant_confirm_id": order.id,
            "error": ClickErrors.SUCCESS,
            "error_note": "Success"
        }
