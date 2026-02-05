from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from fastapi import HTTPException

from app.database.models import User, Product, Order, OrderItem, UserAddress, CartItem
from app.web.schemas.orders import OrderCreateSchema
from app.database.repositories.cart import CartRepository
from app.bot.loader import bot
from app.utils.payment import generate_payme_link

class OrderService:
    @staticmethod
    async def create_order(
        user: User,
        order_data: OrderCreateSchema,
        session: AsyncSession
    ) -> Dict[str, Any]:
        
        # 0. Check Debt
        if user.debt and user.debt > 0:
            raise HTTPException(status_code=403, detail="У вас имеется задолженность. Пожалуйста, погасите её в профиле.")

        # Address handling
        final_address = "Самовывоз: Чиланзар, 1"
        if order_data.delivery_method == "delivery" and order_data.address:
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
            except: pass
            return {"status": "redirect", "url": payme_url}
        if order_data.payment_method == "click":
            return {"status": "success", "order_id": new_order.id}
        else:
            try:
                msg = f"✅ <b>Заказ #{new_order.id} принят!</b>\n💰 {total_amount} сум\n📍 {final_address}\nОплата наличными при получении."
                if user.telegram_id:
                    await bot.send_message(user.telegram_id, msg, parse_mode="HTML")
            except: pass
            return {"status": "success", "order_id": new_order.id}
