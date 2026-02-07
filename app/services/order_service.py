from typing import List, Optional, Dict, Any
import logging
import re
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.config import settings
from app.database.models import User, Product, Order, OrderItem, UserAddress, CartItem
from app.web.schemas.orders import OrderCreateSchema
from app.database.repositories.cart import CartRepository
from app.bot.loader import bot
from app.utils.payment import generate_payme_link

logger = logging.getLogger(__name__)

class OrderService:
    @staticmethod
    def _online_payment_timeout_cutoff() -> datetime:
        timeout_minutes = getattr(settings, "ORDER_PAYMENT_TIMEOUT_MINUTES", 20)
        return datetime.utcnow() - timedelta(minutes=timeout_minutes)

    @staticmethod
    async def cancel_expired_online_orders(
        session: AsyncSession,
        user_id: Optional[int] = None,
    ) -> List[int]:
        cutoff = OrderService._online_payment_timeout_cutoff()
        stmt = select(Order.id).where(
            Order.status == "new",
            Order.payment_method.in_(("card", "click")),
            Order.created_at < cutoff,
        )
        if user_id is not None:
            stmt = stmt.where(Order.user_id == user_id)
        order_ids = (await session.execute(stmt)).scalars().all()

        for order_id in order_ids:
            await OrderService.cancel_order(session, order_id)

        if order_ids:
            await session.commit()

        return order_ids

    @staticmethod
    async def cancel_expired_online_order(
        session: AsyncSession,
        order: Order,
    ) -> bool:
        if (
            order.status == "new"
            and order.payment_method in ("card", "click")
            and order.created_at < OrderService._online_payment_timeout_cutoff()
        ):
            await OrderService.cancel_order(session, order.id)
            await session.commit()
            return True
        return False

    @staticmethod
    async def create_order(
        user: User,
        order_data: OrderCreateSchema,
        session: AsyncSession
    ) -> Dict[str, Any]:
        
        phone_value = (order_data.phone or "").strip()
        if not phone_value:
            raise HTTPException(status_code=400, detail="Укажите номер телефона")
        digits = re.sub(r"\D", "", phone_value)
        if len(digits) < 9:
            raise HTTPException(status_code=400, detail="Некорректный номер телефона")

        # 0. Check Debt
        if user.debt and user.debt > 0:
            raise HTTPException(status_code=403, detail="У вас имеется задолженность. Пожалуйста, погасите её в профиле.")

        await OrderService.cancel_expired_online_orders(session, user_id=user.id)
        cutoff = OrderService._online_payment_timeout_cutoff()

        # Prevent multiple unpaid online orders
        pending_online_order_stmt = select(Order).where(
            Order.user_id == user.id,
            Order.status == "new",
            Order.payment_method.in_(("card", "click")),
            Order.created_at >= cutoff,
        )
        if (await session.execute(pending_online_order_stmt)).scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="У вас есть неоплаченный заказ — сначала оплатите или отмените его",
            )

        # Address handling
        if order_data.delivery_method == "delivery" and not order_data.address:
            raise HTTPException(status_code=400, detail="Адрес обязателен для доставки")
        if order_data.delivery_method == "pickup":
            final_address = "Самовывоз: Чиланзар, 1"
        else:
            final_address = order_data.address
            # Update/Save address if new
            stmt = select(UserAddress).where(UserAddress.user_id == user.id, UserAddress.address_text == order_data.address)
            if not (await session.execute(stmt)).scalar_one_or_none():
                session.add(UserAddress(user_id=user.id, address_text=order_data.address))

        # 1. Get Cart Items & IDOR Check
        cart_repo = CartRepository(session)
        # Fetch only items belonging to this user
        cart_items = await cart_repo.get_items_by_ids(order_data.item_ids, user.id)
        
        # IDOR Check: Ensure all requested items were found and belong to the user
        if len(cart_items) != len(order_data.item_ids):
            # If lengths differ, it means some IDs were not found for this user
            raise HTTPException(status_code=400, detail="Invalid cart items requested")

        if not cart_items:
            raise HTTPException(status_code=400, detail="Cart is empty")

        items_to_delete = []
        for item in cart_items:
            if not item.product:
                items_to_delete.append(item)

        if items_to_delete:
            for item in items_to_delete:
                await session.delete(item)
            await session.commit()
            raise HTTPException(status_code=400, detail="Товар больше недоступен")

        total_amount = 0
        items_to_process = []

        # 2. Atomic Stock Update
        for item in cart_items:
            # Проверяем, не снят ли товар с продажи (Soft Delete)
            if not item.product.is_active:
                raise HTTPException(status_code=400, detail=f"Товар '{item.product.name_ru}' снят с продажи")
            
            # Atomic update: decrement stock only if stock >= quantity
            stmt = (
                update(Product)
                .where(Product.id == item.product_id, Product.stock >= item.quantity)
                .values(stock=Product.stock - item.quantity)
                .execution_options(synchronize_session="fetch")
            )
            result = await session.execute(stmt)
            
            if result.rowcount == 0:
                # Failed to update means out of stock or product missing
                # We should check which one for better error message, but generally it's stock
                # For better UX, we could fetch the product to see actual name, but let's fail fast first
                # Or we can do a check before, but race condition might happen in between.
                # The atomic update guarantees consistency.
                
                # Let's fetch the product name to show a nice error
                prod = await session.get(Product, item.product_id)
                name = prod.name_ru if prod else f"ID {item.product_id}"
                stock = prod.stock if prod else 0
                raise HTTPException(status_code=400, detail=f"Товара '{name}' недостаточно (осталось {stock})")
            
            # If successful, calculate price using the product attached to cart item
            # CAUTION: The product attached to cart_item might be stale in session if not refreshed,
            # but usually it's fine. Safer to use current price. 
            # Since we just updated it, we can trust the price from the 'product' relation loaded in 'cart_items'
            # provided 'cart_repo.get_items_by_ids' loaded valid products.
            
            # However, 'update' doesn't return the price. The 'item.product' is loaded.
            total_amount += item.product.price * item.quantity
            items_to_process.append(item)

        if total_amount <= 0:
            raise HTTPException(status_code=400, detail="Сумма заказа должна быть больше нуля")

        # 3. Create Order
        new_order = Order(
            user_id=user.id, 
            status="new", 
            payment_method=order_data.payment_method,
            delivery_method=order_data.delivery_method, 
            delivery_address=final_address,
            total_amount=total_amount, 
            comment=order_data.comment, 
            contact_phone=order_data.phone
        )
        session.add(new_order)
        await session.flush() # get ID

        # 4. Create Order Items & Clear Cart (Conditional)
        for item in items_to_process:
            session.add(OrderItem(
                order_id=new_order.id, 
                product_id=item.product.id,
                product_name=item.product.name_ru, 
                price_at_purchase=item.product.price, 
                quantity=item.quantity
            ))
            
            # Only delete from cart immediately for offline payments (cash/debt/etc).
            # For online payments (card/click), keep cart items until payment success callback.
            if order_data.payment_method not in ("card", "click"):
                await session.delete(item)
        
        await session.commit()

        # 5. Notifications
        payme_url = None
        if order_data.payment_method == "card":
            payme_url = generate_payme_link(new_order.id, total_amount)
            try:
                msg = f"💳 <b>Заказ #{new_order.id} создан!</b>\nОжидаем оплату: {total_amount} сум."
                if user.telegram_id:
                    await bot.send_message(user.telegram_id, msg, parse_mode="HTML")
            except Exception:
                logger.exception("Failed to send payme notification")
            return {"status": "redirect", "url": payme_url}
        if order_data.payment_method == "click":
            return {"status": "success", "order_id": new_order.id}
        else:
            try:
                msg = f"✅ <b>Заказ #{new_order.id} принят!</b>\n💰 {total_amount} сум\n📍 {final_address}\nОплата наличными при получении."
                if user.telegram_id:
                    await bot.send_message(user.telegram_id, msg, parse_mode="HTML")
            except Exception:
                logger.exception("Failed to send order notification")
            return {"status": "success", "order_id": new_order.id}

    @staticmethod
    async def cancel_order(session: AsyncSession, order_id: int) -> Optional[Order]:
        stmt = (
            select(Order)
            .options(
                selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.user),
            )
            .where(Order.id == order_id)
            .with_for_update()
        )
        order = (await session.execute(stmt)).scalar_one_or_none()

        if not order:
            return None

        if order.status == "cancelled":
            return order

        if order.order_type == "product":
            for item in order.items:
                if item.product_id:
                    await session.execute(
                        update(Product)
                        .where(Product.id == item.product_id)
                        .values(stock=Product.stock + item.quantity)
                        .execution_options(synchronize_session="fetch")
                    )

        if order.order_type == "debt_repayment" and order.status in ("paid", "done"):
            await session.execute(
                update(User)
                .where(User.id == order.user_id)
                .values(debt=func.coalesce(User.debt, 0) + order.total_amount)
                .execution_options(synchronize_session="fetch")
            )

        order.status = "cancelled"
        return order
