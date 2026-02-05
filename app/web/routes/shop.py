from typing import List
from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, update
from sqlalchemy.orm import selectinload

from app.database.core import get_db
from app.database.models import User, Product, Category, CartItem, Order, OrderItem, UserAddress, Favorite
from app.bot.loader import bot
from app.utils.security import check_telegram_auth
from app.utils.payment import generate_payme_link, generate_click_link

from app.utils.csrf import generate_csrf_token, validate_csrf_header
from app.web.schemas.orders import OrderCreateSchema
from app.database.repositories.users import UserRepository
from app.database.repositories.products import ProductRepository
from app.database.repositories.orders import OrderRepository
from app.database.repositories.cart import CartRepository

router = APIRouter(prefix="/shop", tags=["shop"])
templates = Jinja2Templates(directory="app/templates")

# Simple In-Memory Rate Limiter for Order Creation
import time
_order_rate_limit: dict = {}  # user_id -> last_order_timestamp

def check_rate_limit(user_id: int, cooldown_seconds: int = 10) -> bool:
    """Returns True if user is rate limited (should block), False if OK."""
    now = time.time()
    last_order = _order_rate_limit.get(user_id, 0)
    if now - last_order < cooldown_seconds:
        return True  # Rate limited
    _order_rate_limit[user_id] = now
    return False  # OK

@router.post("/auth")
async def auth_user(request: Request, initData: str = Form(...), session: AsyncSession = Depends(get_db)):
    tg_user = check_telegram_auth(initData)
    
    if not tg_user:
        return JSONResponse({"status": "error", "message": "Invalid hash"}, status_code=403)
    
    tg_id = tg_user['id']
    
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(tg_id)
    
    if not user:
        user = User(telegram_id=tg_id, username=tg_user.get('username'), role="user")
        user_repo.add(user)
        await user_repo.commit()

    request.session["shop_user_id"] = user.id
    return {"status": "ok"}

async def get_shop_user(request: Request, session: AsyncSession = Depends(get_db)):
    user_id = request.session.get("shop_user_id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    
    # We need to load addresses, usually repo handles this or we rely on lazy loading/separate query if needed
    # But for now, let's use direct query if Repo doesn't support eager load explicitly or update Repo
    # Or just use the session from repo
    # Ideally Repo should have get_by_id_with_related or similar.
    # For now, I'll use repo.get_by_id but we need addresses.
    # Let's keep it simple and assume we might need to add get_full_profile to UserRepository
    stmt = select(User).options(selectinload(User.addresses)).where(User.id == user_id)
    user = (await session.execute(stmt)).scalar_one_or_none()
    
    if not user:
         raise HTTPException(status_code=401, detail="User not found")
         
    return user

@router.get("/", response_class=HTMLResponse)
async def shop_index(request: Request, session: AsyncSession = Depends(get_db)):
    try:
        user = await get_shop_user(request, session)
    except HTTPException:
        return templates.TemplateResponse("shop/auth_loader.html", {"request": request})

    categories = (await session.execute(select(Category))).scalars().all()
    product_repo = ProductRepository(session)
    products = await product_repo.get_active()
    
    csrf_token = generate_csrf_token(request)

    return templates.TemplateResponse("shop/index.html", {
        "request": request, "user": user, "categories": categories, "products": products, "csrf_token": csrf_token
    })

@router.get("/set_lang")
async def set_language(lang: str, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    if lang in ["ru", "uz"]:
        user.language = lang
        await session.commit()
    return RedirectResponse("/shop", status_code=303)

@router.get("/cart", response_class=HTMLResponse)
async def view_cart(request: Request, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    cart_repo = CartRepository(session)
    cart_items = await cart_repo.get_by_user(user.id)
    
    # Logic to handle ghost items
    final_items = []
    items_to_delete = []
    
    for item in cart_items:
        if not item.product:
            items_to_delete.append(item)
        else:
            # Monkey-patch unavailable flag for template
            item.unavailable = not item.product.is_active
            final_items.append(item)
            
    if items_to_delete:
        for i in items_to_delete:
            await session.delete(i)
        await session.commit()
    
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("shop/cart.html", {"request": request, "user": user, "cart_items": final_items, "csrf_token": csrf_token})

@router.post("/api/cart/add/{product_id}", dependencies=[Depends(validate_csrf_header)])
async def add_to_cart(product_id: int, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    # Проверяем, есть ли товар вообще
    product_repo = ProductRepository(session)
    product = await product_repo.get_by_id(product_id)
    if not product or not product.is_active or product.stock <= 0:
         return JSONResponse({"success": False, "message": "Out of stock"}, status_code=400)

    cart_repo = CartRepository(session)
    existing = await cart_repo.get_item(user.id, product_id)
    
    if existing:
        # Проверяем, не превышает ли лимит на складе
        if existing.quantity >= product.stock:
            return JSONResponse({"success": False, "message": "Больше нет в наличии"}, status_code=400)
        
        # Atomic update to prevent race conditions
        stmt_update = (
            update(CartItem)
            .where(CartItem.id == existing.id)
            .values(quantity=CartItem.quantity + 1)
        )
        await session.execute(stmt_update)
    else:
        session.add(CartItem(user_id=user.id, product_id=product_id, quantity=1))
        
    await session.commit()
    count_stmt = select(CartItem).where(CartItem.user_id == user.id)
    items = (await session.execute(count_stmt)).scalars().all()
    return {"success": True, "total_count": sum(i.quantity for i in items)}

@router.post("/api/cart/update/{item_id}", dependencies=[Depends(validate_csrf_header)])
async def update_cart_qty(item_id: int, qty: int, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    cart_repo = CartRepository(session)
    item = await cart_repo.get_by_id_and_user(item_id, user.id)
    
    if item and qty > 0:
        if qty > item.product.stock:
             return JSONResponse({"success": False, "message": "Not enough stock"}, status_code=400)
        
        # Atomic update
        stmt = update(CartItem).where(CartItem.id == item_id).values(quantity=qty)
        await session.execute(stmt)
        await session.commit()
    return {"success": True}

@router.post("/api/cart/delete/{item_id}", dependencies=[Depends(validate_csrf_header)])
async def delete_cart_item(item_id: int, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    cart_repo = CartRepository(session)
    item = await cart_repo.get_by_id_and_user(item_id, user.id)
    if item:
        await session.delete(item)
        await session.commit()
    return {"success": True}

@router.get("/api/search")
async def search_products(request: Request, q: str = "", user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    product_repo = ProductRepository(session)
    products = await product_repo.search(q)
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("shop/partials/product_list.html", {"request": request, "user": user, "products": products, "csrf_token": csrf_token})

@router.get("/api/products")
async def get_products(request: Request, category_id: str = "all", user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    product_repo = ProductRepository(session)
    if category_id and category_id.isdigit():
        products = await product_repo.get_by_category(int(category_id))
    else:
        products = await product_repo.get_active()
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("shop/partials/product_list.html", {"request": request, "user": user, "products": products, "csrf_token": csrf_token})

@router.get("/favorites", response_class=HTMLResponse)
async def view_favorites(request: Request, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    stmt = select(Product).join(Favorite).where(Favorite.user_id == user.id)
    products = (await session.execute(stmt)).scalars().all()
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("shop/favorites.html", {"request": request, "user": user, "products": products, "csrf_token": csrf_token})

@router.post("/api/favorite/{product_id}", dependencies=[Depends(validate_csrf_header)])
async def toggle_favorite(product_id: int, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    stmt = select(Favorite).where(Favorite.user_id == user.id, Favorite.product_id == product_id)
    fav = (await session.execute(stmt)).scalar_one_or_none()
    added = False
    if fav:
        await session.delete(fav)
    else:
        session.add(Favorite(user_id=user.id, product_id=product_id))
        added = True
    await session.commit()
    return {"success": True, "added": added}

@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(request: Request, items: List[int] = Query(None), user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    if not items: return RedirectResponse("/shop/cart")
    cart_repo = CartRepository(session)
    selected_items = await cart_repo.get_items_by_ids(items, user.id)
    if not selected_items: return RedirectResponse("/shop/cart")
    total_amount = sum(item.product.price * item.quantity for item in selected_items)
    total_count = sum(item.quantity for item in selected_items)
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("shop/checkout.html", {"request": request, "user": user, "item_ids": items, "total_amount": total_amount, "total_count": total_count, "csrf_token": csrf_token})
     

@router.post("/order/create", dependencies=[Depends(validate_csrf_header)])
async def create_order(
    request: Request,
    order_data: OrderCreateSchema = Depends(OrderCreateSchema.as_form),
    user: User = Depends(get_shop_user),
    session: AsyncSession = Depends(get_db)
):
    # Rate Limiting: не более 1 заказа в 10 секунд
    if check_rate_limit(user.id, cooldown_seconds=10):
        return JSONResponse({"status": "error", "message": "Подождите немного перед созданием нового заказа"}, status_code=429)
    
    from app.services.order_service import OrderService
    try:
        result = await OrderService.create_order(user, order_data, session)
        
        # Если метод оплаты click
        if order_data.payment_method == "click":
            # order_service возвращает JSONResponse с ID заказа, но нам нужно перехватить
            # В данном случае OrderService.create_order возвращает dict, если посмотреть код в сервисе?
            # Нет, в shop.py строка 246: return JSONResponse(result)
            # Значит result это dict.

            # ВАЖНО: OrderService.create_order возвращает dict, например {"status": "success", "order_id": 123}
            # Если status == success, то генерим ссылку.
            
            if result.get("status") == "success":
                order_id = result.get("order_id")
                # Нужно получить сумму заказа. result может не содержать сумму.
                # Лучше запросить заказ или изменить сервис.
                # Но чтобы не менять сервис, запросим заказ.
                stmt = select(Order).where(Order.id == order_id)
                new_order = (await session.execute(stmt)).scalar_one()
                
                click_url = generate_click_link(new_order.id, new_order.total_amount)
                
                # Уведомление
                try:
                    msg = f"💳 <b>Заказ #{new_order.id} создан!</b>\nОжидаем оплату через Click: {new_order.total_amount} сум."
                    if user.telegram_id:
                        await bot.send_message(user.telegram_id, msg, parse_mode="HTML")
                except: pass
                
                return JSONResponse({"status": "redirect", "url": click_url})
        
        return JSONResponse(result)
    except HTTPException as e:
        return JSONResponse({"status": "error", "message": e.detail}, status_code=e.status_code)
    except Exception as e:
        # Log error here in future
        print(f"Order Creation Error: {e}") 
        return JSONResponse({"status": "error", "message": "Произошла ошибка при создании заказа"}, status_code=500)

@router.post("/order/pay_debt")
async def create_debt_payment(
    request: Request,
    amount: int = Form(...),
    user: User = Depends(get_shop_user),
    session: AsyncSession = Depends(get_db)
):
    if user.debt <= 0:
         return JSONResponse({"status": "error", "message": "У вас нет долгов"}, status_code=400)
         
    if amount <= 0:
        return JSONResponse({"status": "error", "message": "Неверная сумма"}, status_code=400)

    # Проверка суммы погашения (нельзя оплатить больше, чем долг)
    if user.debt and amount > user.debt:
        return JSONResponse({"status": "error", "message": f"Сумма превышает ваш долг ({user.debt})"}, status_code=400)
    
    # Extra safety: Ensure debt is strictly positive
    if not user.debt or user.debt <= 0:
         return JSONResponse({"status": "error", "message": "У вас нет долгов"}, status_code=400)

    # Создаем заказ на погашение долга
    new_order = Order(
        user_id=user.id,
        status="new",
        order_type="debt_repayment",
        payment_method="card",
        delivery_method="none",
        delivery_address=None,
        total_amount=amount,
        comment="Погашение долга",
        contact_phone=user.phone or ""
    )
    session.add(new_order)
    await session.commit()
    
    payme_url = generate_payme_link(new_order.id, amount)
    return JSONResponse({"status": "redirect", "url": payme_url})

@router.get("/order/success/{order_id}", response_class=HTMLResponse)
async def order_success_page(request: Request, order_id: int, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    # IDOR Check: Ensure order belongs to user
    stmt = select(Order).where(Order.id == order_id, Order.user_id == user.id)
    order = (await session.execute(stmt)).scalar_one_or_none()
    
    if not order:
        return RedirectResponse("/shop/profile")
        
    return templates.TemplateResponse("shop/order_success.html", {"request": request, "user": user, "order_id": order_id})

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(get_shop_user), session: AsyncSession = Depends(get_db)):
    stmt = select(Order).where(Order.user_id == user.id).order_by(Order.created_at.desc())
    orders = (await session.execute(stmt)).scalars().all()
    return templates.TemplateResponse("shop/profile.html", {"request": request, "user": user, "orders": orders})