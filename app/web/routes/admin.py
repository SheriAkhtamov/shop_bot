import os
import shutil
import uuid
import asyncio
from typing import Optional, List

from fastapi import APIRouter, Request, Form, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from aiogram.types import BufferedInputFile

import aiofiles
from PIL import Image
from io import BytesIO
from app.utils.csrf import generate_csrf_token, validate_csrf
from app.utils.file_manager import delete_file

from app.database.core import get_db
from app.database.models import User, Category, Order, OrderItem
from app.utils.security import verify_password
from app.bot.loader import bot
from sqlalchemy import or_, func

from app.database.repositories.users import UserRepository
from app.database.repositories.products import ProductRepository
from app.database.repositories.orders import OrderRepository

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

async def get_current_admin(request: Request, session: AsyncSession = Depends(get_db)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)
    
    if user and user.role in ["manager", "superadmin"]:
        return user
    return None

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    csrf_token = generate_csrf_token(request)
    return templates.TemplateResponse("admin/login.html", {"request": request, "csrf_token": csrf_token})

@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    user_repo = UserRepository(session)
    user = await user_repo.get_by_login(username)

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("admin/login.html", {
            "request": request, 
            "error": "Неверный логин или пароль"
        })
    
    if user.role not in ["manager", "superadmin"]:
         return templates.TemplateResponse("admin/login.html", {
            "request": request, 
            "error": "У вас нет прав доступа"
        })

    request.session["user_id"] = user.id
    return RedirectResponse(url="/admin", status_code=303)

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login")

@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request, 
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse(url="/admin/login")

    # 1. KPI: Users Count
    users_count_stmt = select(func.count(User.id)).where(User.role == "user")
    users_count = (await session.execute(users_count_stmt)).scalar() or 0

    # 2. KPI: Orders Today
    from datetime import datetime, date
    today = date.today()
    orders_today_stmt = select(func.count(Order.id)).where(
        func.date(Order.created_at) == today,
        Order.status != 'cancelled'
    )
    orders_today = (await session.execute(orders_today_stmt)).scalar() or 0

    # 3. KPI: Revenue Month
    current_month = today.month
    current_year = today.year
    revenue_stmt = select(func.sum(Order.total_amount)).where(
        Order.status.in_(['done', 'paid']),
        func.extract('month', Order.created_at) == current_month,
        func.extract('year', Order.created_at) == current_year
    )
    revenue_month = (await session.execute(revenue_stmt)).scalar() or 0

    # 4. KPI: Total Debt
    debt_stmt = select(func.sum(User.debt)).where(User.role == "user", User.debt > 0)
    total_debt = (await session.execute(debt_stmt)).scalar() or 0

    # --- CHARTS DATA ---
    
    # Chart 1: Monthly Sales (Last 6 months)
    # We need to construct a robust query or just python-process it if data is small. 
    # Let's use SQL for aggregation.
    from sqlalchemy import text
    
    # Simple grouping by month (Postgres specific syntax might be needed, but generic SQL usually works)
    # Using raw sql for complex date truncation is often easier with sqlalchemy
    monthly_sales_stmt = text("""
        SELECT 
            TO_CHAR(created_at, 'YYYY-MM') as month_label,
            COUNT(id) as order_count,
            SUM(total_amount) as total_revenue
        FROM orders
        WHERE status IN ('done', 'paid') AND created_at > current_date - interval '6 months'
        GROUP BY month_label
        ORDER BY month_label ASC
    """)
    # Note: 'orders' table name assumption. Let's check model tablename. 
    # Usually it's 'orders' if class is Order. 
    # To be safe, let's use SQLAlchemy Core expression which handles table names.
    
    # SQLAlchemy expression for Monthly Sales
    # extract('month', ...) returns int, so we need year too.
    # Grouping by Year, Month
    sales_stmt = (
        select(
            func.extract('year', Order.created_at).label('year'),
            func.extract('month', Order.created_at).label('month'),
            func.count(Order.id).label('count'),
            func.sum(Order.total_amount).label('sum')
        )
        .where(Order.status.in_(['done', 'paid']))
        .group_by('year', 'month')
        .order_by('year', 'month')
        .limit(12) 
    )
    sales_data_raw = (await session.execute(sales_stmt)).all()
    
    # Format for Chart.js
    monthly_labels = []
    monthly_rev = []
    monthly_count = []
    months_map = {1:'Jan', 2:'Feb', 3:'Mar', 4:'Apr', 5:'May', 6:'Jun', 7:'Jul', 8:'Aug', 9:'Sep', 10:'Oct', 11:'Nov', 12:'Dec'}
    
    for row in sales_data_raw:
        m_name = months_map.get(int(row.month), str(row.month))
        monthly_labels.append(f"{m_name} {int(row.year)}")
        monthly_rev.append(int(row.sum))
        monthly_count.append(int(row.count))


    # Chart 2: Top Products
    # Need to join Order and OrderItem (assuming relationship exists or manual join)
    # Let's check models.py later, but assuming OrderItem has product_name and quantity.
    from app.database.models import OrderItem
    top_products_stmt = (
        select(OrderItem.product_name, func.sum(OrderItem.quantity).label('total_qty'))
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.status.in_(['done', 'paid']))
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(5)
    )
    top_products_raw = (await session.execute(top_products_stmt)).all()
    
    top_prod_labels = [row.product_name for row in top_products_raw]
    top_prod_data = [int(row.total_qty) for row in top_products_raw]

    # Chart 3: Payment Methods
    pay_methods_stmt = (
        select(Order.payment_method, func.count(Order.id))
        .where(Order.status != 'cancelled')
        .group_by(Order.payment_method)
    )
    pay_methods_raw = (await session.execute(pay_methods_stmt)).all()
    
    pay_labels_map = {"card": "Payme", "cash": "Наличные", "debt_repayment": "Погашение долга"}
    pay_labels = [pay_labels_map.get(row.payment_method, row.payment_method) for row in pay_methods_raw]
    pay_data = [row[1] for row in pay_methods_raw]


    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user": user,
        "csrf_token": generate_csrf_token(request),
        
        # KPIs
        "users_count": users_count,
        "orders_today": orders_today,
        "revenue_month": f"{revenue_month:,}".replace(",", " "), # Format 10 000
        "total_debt": f"{total_debt:,}".replace(",", " "),

        # Charts
        "monthly_labels": monthly_labels,
        "monthly_rev": monthly_rev,
        "monthly_count": monthly_count,
        "top_prod_labels": top_prod_labels,
        "top_prod_data": top_prod_data,
        "pay_labels": pay_labels,
        "pay_data": pay_data
    })

@router.get("/products", response_class=HTMLResponse)
async def products_list(
    request: Request,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    product_repo = ProductRepository(session)
    products = await product_repo.get_all() # Note: get_all selects all, original had order by id desc

    return templates.TemplateResponse("admin/products_list.html", {
        "request": request,
        "user": user,
        "products": products,
        "csrf_token": generate_csrf_token(request)
    })

@router.get("/products/new", response_class=HTMLResponse)
async def product_create_form(
    request: Request,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    stmt = select(Category)
    categories = (await session.execute(stmt)).scalars().all()

    return templates.TemplateResponse("admin/product_edit.html", {
        "request": request,
        "user": user,
        "categories": categories,
        "product": None,
        "csrf_token": generate_csrf_token(request)
    })

from app.web.schemas.products import ProductCreateSchema
from fastapi import UploadFile, File

@router.post("/products/new")
async def product_create_save(
    request: Request,
    product_data: ProductCreateSchema = Depends(ProductCreateSchema.as_form),
    image: UploadFile = File(None),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    if not user: return RedirectResponse("/admin/login")

    image_path = ""
    
    if image and image.filename:
        extension = image.filename.split(".")[-1]
        unique_name = f"{uuid.uuid4()}.{extension}"
        
        upload_dir = "media/products"
        await asyncio.to_thread(os.makedirs, upload_dir, exist_ok=True)
        
        file_location = f"{upload_dir}/{unique_name}"
        
        # Validate Image with PIL
        try:
            file_bytes = await image.read()
            img = Image.open(BytesIO(file_bytes))
            img.verify() # Verify it's an image
            await image.seek(0) # Reset cursor
        except Exception:
             # Redirect with error flag
             return RedirectResponse("/admin/products/new?error=invalid_image", status_code=303)

        file_location = f"{upload_dir}/{unique_name}"
        
        async with aiofiles.open(file_location, "wb") as buffer:
            await buffer.write(file_bytes)
            
        image_path = f"/media/products/{unique_name}"

    new_product = Product(
        name_ru=product_data.name_ru,
        name_uz=product_data.name_uz,
        category_id=product_data.category_id,
        price=product_data.price,
        stock=product_data.stock,
        description_ru=product_data.description_ru,
        description_uz=product_data.description_uz,
        ikpu=product_data.ikpu,
        package_code=product_data.package_code,
        image_path=image_path,
        is_active=True
    )
    
    product_repo = ProductRepository(session)
    product_repo.add(new_product)
    
    try:
        await product_repo.commit()
    except Exception as e:
        # Очистка мусорного файла при ошибке записи в БД
        if image_path:
            await delete_file(image_path)
        return RedirectResponse("/admin/products/new?error=db_error", status_code=303)

    return RedirectResponse("/admin/products", status_code=303)

@router.get("/products/{product_id}/edit", response_class=HTMLResponse)
async def product_edit_form(
    request: Request,
    product_id: int,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    product_repo = ProductRepository(session)
    product = await product_repo.get_by_id(product_id)
    
    if not product:
        return RedirectResponse("/admin/products")

    stmt = select(Category)
    categories = (await session.execute(stmt)).scalars().all()

    return templates.TemplateResponse("admin/product_edit.html", {
        "request": request,
        "user": user,
        "categories": categories,
        "product": product,
        "csrf_token": generate_csrf_token(request)
    })

@router.post("/products/{product_id}/edit")
async def product_edit_save(
    request: Request,
    product_id: int,
    product_data: ProductCreateSchema = Depends(ProductCreateSchema.as_form),
    image: UploadFile = File(None),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    if not user: return RedirectResponse("/admin/login")
    
    product_repo = ProductRepository(session)
    product = await product_repo.get_with_lock(product_id)
    
    if not product:
        return RedirectResponse("/admin/products")
        
    # Update fields
    product.name_ru = product_data.name_ru
    product.name_uz = product_data.name_uz
    product.category_id = product_data.category_id
    product.price = product_data.price
    product.stock = product_data.stock
    product.description_ru = product_data.description_ru
    product.description_uz = product_data.description_uz
    product.ikpu = product_data.ikpu
    product.package_code = product_data.package_code
    
    if image and image.filename:
        # Delete old image
        await delete_file(product.image_path)
        
        # Save new image
        extension = image.filename.split(".")[-1]
        unique_name = f"{uuid.uuid4()}.{extension}"
        upload_dir = "media/products"
        await asyncio.to_thread(os.makedirs, upload_dir, exist_ok=True)
        file_location = f"{upload_dir}/{unique_name}"
        
        # Validate Image with PIL
        try:
            file_bytes = await image.read()
            img = Image.open(BytesIO(file_bytes))
            img.verify()
            await image.seek(0)
        except Exception:
            return RedirectResponse(f"/admin/products/{product_id}/edit?error=invalid_image", status_code=303)

        async with aiofiles.open(file_location, "wb") as buffer:
            await buffer.write(file_bytes)
            
        product.image_path = f"/media/products/{unique_name}"
        
    try:
        await session.commit()
    except Exception as e:
        # Если загрузили новое изображение, удаляем его при ошибке
        if image and image.filename and product.image_path:
            await delete_file(product.image_path)
        return RedirectResponse(f"/admin/products/{product_id}/edit?error=db_error", status_code=303)
        
    return RedirectResponse("/admin/products", status_code=303)

@router.post("/products/delete/{product_id}")
async def product_delete(
    product_id: int,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    request: Request = None,
    csrf: bool = Depends(validate_csrf)
):
    if not user: return RedirectResponse("/admin/login")
    
    product_repo = ProductRepository(session)
    product = await product_repo.get_by_id(product_id)
    
    if product:
        # Soft Delete: помечаем как архивный вместо удаления
        # Это предотвращает ошибки связей с заказами и корзинами
        product.is_active = False
        # НЕ удаляем файл изображения — товар остается в истории заказов
        await session.commit()
        
    return RedirectResponse("/admin/products", status_code=303)

@router.get("/orders", response_class=HTMLResponse)
async def orders_list(
    request: Request,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    page = 1
    try:
        page = int(request.query_params.get("page", 1))
    except: pass
    
    limit = 20
    offset = (page - 1) * limit
    
    order_repo = OrderRepository(session)
    total_count = await order_repo.count()
    orders = await order_repo.get_all_detailed(limit=limit, offset=offset)
    
    total_pages = (total_count + limit - 1) // limit

    return templates.TemplateResponse("admin/orders_list.html", {
        "request": request, 
        "user": user, 
        "orders": orders,
        "page": page,
        "total_pages": total_pages,
        "csrf_token": generate_csrf_token(request)
    })

@router.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(
    request: Request,
    order_id: int,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    order_repo = OrderRepository(session)
    order = await order_repo.get_full_info(order_id)
    
    if not order:
        return RedirectResponse("/admin/orders")

    return templates.TemplateResponse("admin/order_detail.html", {
        "request": request, 
        "user": user, 
        "order": order,
        "csrf_token": generate_csrf_token(request)
    })

@router.post("/orders/{order_id}/status")
async def order_change_status(
    order_id: int,
    status: str = Form(...),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    request: Request = None,
    csrf: bool = Depends(validate_csrf)
):
    if not user: return RedirectResponse("/admin/login")

    if status == "cancelled":
        return RedirectResponse(f"/admin/orders/{order_id}", status_code=303)

    order_repo = OrderRepository(session)
    order = await order_repo.get_with_lock(order_id)
    
    if order:
        order.status = status
        await session.commit()
        
        status_text = {
            "delivery": "🚚 Ваш заказ передан курьеру!",
            "done": "✅ Ваш заказ успешно доставлен. Спасибо за покупку!",
            "cancelled": "❌ Ваш заказ был отменен."
        }.get(status)

        if status_text and order.user.telegram_id:
            try:
                await bot.send_message(order.user.telegram_id, status_text)
            except Exception as e:
                # Пользователь мог заблокировать бота — игнорируем
                pass

    return RedirectResponse(f"/admin/orders/{order_id}", status_code=303)

@router.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    q: str = "",
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if not user: return RedirectResponse("/admin/login")

    # For user search we might need a specific method in repo or just keep raw sql for complex filter
    # But let's add a search method to UserRepository
    repo = UserRepository(session)
    if q:
        # We need to implement search in repo, but for now I'll just use the repo's session
        stmt = select(User).order_by(User.id.desc()).where(or_(User.username.ilike(f"%{q}%"), User.phone.ilike(f"%{q}%")))
        users = (await session.execute(stmt)).scalars().all()
    else:
        # Filter by role='user'
        stmt = select(User).where(User.role == "user").order_by(User.id.desc())
        users = (await session.execute(stmt)).scalars().all()

    return templates.TemplateResponse("admin/users_list.html", {
        "request": request,
        "user": user,
        "users": users,
        "csrf_token": generate_csrf_token(request)
    })

@router.post("/users/{user_id}/set_debt")
async def user_set_debt(
    user_id: int,
    amount: int = Form(...),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    if not user: return RedirectResponse("/admin/login")
    
    repo = UserRepository(session)
    target_user = await repo.get_with_lock(user_id)
    
    if target_user:
        target_user.debt = amount
        await session.commit()
        
    return RedirectResponse("/admin/users", status_code=303)

@router.get("/managers", response_class=HTMLResponse)
async def managers_list(
    request: Request,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db)
):
    if user.role != "superadmin":
        return templates.TemplateResponse("admin/error.html", {"request": request, "message": "Доступ запрещен"})

    repo = UserRepository(session)
    managers = await repo.get_admins()

    return templates.TemplateResponse("admin/managers_list.html", {
        "request": request, 
        "user": user, 
        "managers": managers,
        "csrf_token": generate_csrf_token(request)
    })

@router.post("/managers/new")
async def manager_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    telegram_id: int = Form(None),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    if user.role != "superadmin":
        return RedirectResponse("/admin")
    
    from app.utils.security import get_password_hash
    pwd_hash = get_password_hash(password)
    
    if not telegram_id:
        # import random
        # telegram_id = random.randint(1000, 999999999)
        # Allow NULL
        telegram_id = None

    new_manager = User(
        username="Менеджер",
        login=username,
        password_hash=pwd_hash,
        role="manager",
        telegram_id=telegram_id
    )
    
    try:
        session.add(new_manager)
        await session.commit()
    except Exception as e:
        logger.error(e)
        
    return RedirectResponse("/admin/managers", status_code=303)

@router.post("/managers/delete/{manager_id}")
async def manager_delete(
    manager_id: int,
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    request: Request = None,
    csrf: bool = Depends(validate_csrf)
):
    if user.role != "superadmin":
        return RedirectResponse("/admin")

    repo = UserRepository(session)
    manager = await repo.get_by_id(manager_id)
    
    if manager and manager.role != "superadmin":
        await session.delete(manager)
        await session.commit()
        
    return RedirectResponse("/admin/managers", status_code=303)

async def perform_mailing(chat_ids: List[int], text: str, photo_bytes: Optional[bytes]):
    file_id = None
    
    for chat_id in chat_ids:
        try:
            if photo_bytes:
                if file_id is None:
                    # Первая отправка — загружаем файл
                    file = BufferedInputFile(photo_bytes, filename="image.jpg")
                    msg = await bot.send_photo(chat_id, photo=file, caption=text)
                    # Сохраняем ID загруженного фото для переиспользования
                    file_id = msg.photo[-1].file_id
                else:
                    # Остальным отправляем по file_id (мгновенно)
                    await bot.send_photo(chat_id, photo=file_id, caption=text)
            else:
                await bot.send_message(chat_id, text)
            await asyncio.sleep(0.05)
        except Exception as e:
            # Пользователь мог заблокировать бота
            pass

@router.get("/mailing", response_class=HTMLResponse)
async def mailing_page(request: Request, user: User = Depends(get_current_admin)):
    if user.role != "superadmin": return RedirectResponse("/admin")
    return templates.TemplateResponse("admin/mailing.html", {"request": request, "user": user, "csrf_token": generate_csrf_token(request)})

@router.post("/mailing/send")
async def mailing_send(
    request: Request,
    background_tasks: BackgroundTasks,
    text: str = Form(...),
    image: UploadFile = File(None),
    user: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_db),
    csrf: bool = Depends(validate_csrf)
):
    if user.role != "superadmin": return RedirectResponse("/admin")

    stmt = select(User.telegram_id).where(
        User.telegram_id.isnot(None),
        User.role == "user"
    )
    ids = (await session.execute(stmt)).scalars().all()

    photo_bytes = None
    if image and image.filename:
        photo_bytes = await image.read()
        try:
            img = Image.open(BytesIO(photo_bytes))
            img.verify()
        except Exception:
            photo_bytes = None # Skip invalid image

    background_tasks.add_task(perform_mailing, ids, text, photo_bytes)

    return templates.TemplateResponse("admin/mailing.html", {
        "request": request, 
        "user": user, 
        "message": f"Рассылка запущена для {len(ids)} пользователей. Она будет выполнена в фоновом режиме."
    })