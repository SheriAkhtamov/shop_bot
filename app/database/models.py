from sqlalchemy import BigInteger, String, ForeignKey, Boolean, Text, Integer, DateTime, func, UniqueConstraint, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship
from typing import List
from decimal import Decimal
from datetime import datetime
from app.database.core import Base

# --- Пользователи и Сотрудники ---
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    username: Mapped[str] = mapped_column(String, nullable=True)
    phone: Mapped[str] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, default="ru") # ru / uz
    
    # Роли: user, manager, superadmin
    role: Mapped[str] = mapped_column(String, default="user")

    # Долг пользователя (в сумах)
    debt: Mapped[int] = mapped_column(Integer, default=0)
    
    # Поля для сотрудников (менеджеров)
    login: Mapped[str] = mapped_column(String, nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    # Связи
    orders: Mapped[List["Order"]] = relationship(back_populates="user")
    addresses: Mapped[List["UserAddress"]] = relationship(back_populates="user")
    favorites: Mapped[List["Favorite"]] = relationship(back_populates="user")

class UserAddress(Base):
    __tablename__ = "user_addresses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    address_text: Mapped[str] = mapped_column(Text)
    
    user: Mapped["User"] = relationship(back_populates="addresses")

# --- Товары ---
class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name_ru: Mapped[str] = mapped_column(String)
    name_uz: Mapped[str] = mapped_column(String)
    
    products: Mapped[List["Product"]] = relationship(back_populates="category")

class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    
    name_ru: Mapped[str] = mapped_column(String, index=True)
    name_uz: Mapped[str] = mapped_column(String, index=True)
    description_ru: Mapped[str] = mapped_column(Text, nullable=True)
    description_uz: Mapped[str] = mapped_column(Text, nullable=True)
    
    price: Mapped[int] = mapped_column(Integer) # Храним в сумах
    stock: Mapped[int] = mapped_column(Integer, default=0)
    image_path: Mapped[str] = mapped_column(String)

    # Новые поля для фискализации
    ikpu: Mapped[str] = mapped_column(String, default="00702001001000001", nullable=True)
    package_code: Mapped[str] = mapped_column(String, default="000000", nullable=True)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped["Category"] = relationship(back_populates="products")
    cart_items: Mapped[List["CartItem"]] = relationship(back_populates="product")
    favorites: Mapped[List["Favorite"]] = relationship(back_populates="product")

# --- Корзина и Избранное ---
class CartItem(Base):
    __tablename__ = "cart_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    
    product: Mapped["Product"] = relationship(back_populates="cart_items")

class Favorite(Base):
    __tablename__ = "favorites"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))

    user: Mapped["User"] = relationship(back_populates="favorites")
    product: Mapped["Product"] = relationship(back_populates="favorites")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'product_id', name='_user_product_favorite_uc'),
    )

# --- Rate Limiting ---
class OrderRateLimit(Base):
    __tablename__ = "order_rate_limits"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

# --- Заказы ---
class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    
    status: Mapped[str] = mapped_column(String, default="new", index=True) # new, paid, delivery, done, cancelled
    order_type: Mapped[str] = mapped_column(String, default="product") # product, debt_repayment
    payment_method: Mapped[str] = mapped_column(String) # cash, card
    delivery_method: Mapped[str] = mapped_column(String) # pickup, delivery
    
    delivery_address: Mapped[str] = mapped_column(Text, nullable=True)
    total_amount: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, nullable=True)
    contact_phone: Mapped[str] = mapped_column(String, index=True)
    
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)

    user: Mapped["User"] = relationship(back_populates="orders")
    items: Mapped[List["OrderItem"]] = relationship(back_populates="order")
    # Связь с транзакциями Payme (может быть несколько попыток оплаты)
    payme_transactions: Mapped[List["PaymeTransaction"]] = relationship(back_populates="order")

class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=True)
    product_name: Mapped[str] = mapped_column(String)
    price_at_purchase: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    stock_before_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()

# --- PAYME ТРАНЗАКЦИИ ---
class PaymeTransaction(Base):
    __tablename__ = "payme_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Payme присылает свой ID транзакции (длинная строка), он должен быть уникальным
    payme_id: Mapped[str] = mapped_column(String, unique=True, index=True) 
    time: Mapped[int] = mapped_column(BigInteger) # Время создания в Payme (timestamp ms)
    amount: Mapped[int] = mapped_column(Integer) # Сумма в тийинах
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    
    # Состояние транзакции (по документации Payme):
    # 1 - Создана (ожидает подтверждения)
    # 2 - Подтверждена (деньги списаны)
    # -1 - Отменена
    # -2 - Отменена после завершения
    state: Mapped[int] = mapped_column(Integer, default=1)
    
    reason: Mapped[int] = mapped_column(Integer, nullable=True) # Причина отмены
    
    create_time: Mapped[datetime] = mapped_column(default=datetime.utcnow) # Наше время создания
    perform_time: Mapped[datetime] = mapped_column(DateTime, nullable=True) # Время подтверждения
    cancel_time: Mapped[datetime] = mapped_column(DateTime, nullable=True) # Время отмены

    order: Mapped["Order"] = relationship(back_populates="payme_transactions")

# --- CLICK ТРАНЗАКЦИИ ---
class ClickTransaction(Base):
    __tablename__ = "click_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    click_trans_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True) # ID транзакции в Click
    service_id: Mapped[int] = mapped_column(Integer)
    click_paydoc_id: Mapped[int] = mapped_column(BigInteger)
    merchant_trans_id: Mapped[str] = mapped_column(String, index=True) # Наш ID заказа
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2)) # Сумма
    action: Mapped[int] = mapped_column(Integer) # 0=Prepare, 1=Complete
    error: Mapped[int] = mapped_column(Integer)
    error_note: Mapped[str] = mapped_column(String, nullable=True)
    sign_time: Mapped[str] = mapped_column(String)
    sign_string: Mapped[str] = mapped_column(String)
    
    status: Mapped[str] = mapped_column(String, default="input") # input, canceled, confirmed
    
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
