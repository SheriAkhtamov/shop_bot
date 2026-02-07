class Settings:
    # --- НАСТРОЙКИ БОТА ---
    SECRET_KEY = "dummy-secret-key-change-me-in-production"
    BOT_TOKEN = "8459371332:AAFOJgNVwKID642txTq8tF7gDk5xfToUIvs"
    
    # ID админов в Telegram (кому придут уведомления о старте, если настроить)
    # Можешь вписать свой ID цифрами, например [12345678]
    ADMIN_IDS = [] 
    
    # --- СУПЕРАДМИН ---
    SUPERADMIN_LOGIN = "unicom"
    SUPERADMIN_PASSWORD = "unicombotadmin2026"
    SYNC_SUPERADMIN_PASSWORD = False

    # --- НАСТРОЙКИ БД ---
    DB_USER = "postgres"
    DB_PASS = "postgres"
    DB_HOST = "db"
    DB_PORT = 5432
    DB_NAME = "shop_db"
    
    # --- НАСТРОЙКИ САЙТА ---
    # HTTPS обязателен для Mini App.
    # Если настраиваешь на сервере с доменом:
    WEB_BASE_URL = "https://unicombot.uz"
    
    # --- НАСТРОЙКИ PAYME (ОПЛАТА) ---
    PAYME_ID = "697b63129eccc7679b552de7"  
    
    # --- НАСТРОЙКИ CLICK ---
    CLICK_SERVICE_ID = "95107"
    CLICK_MERCHANT_ID = "55704"
    CLICK_SECRET_KEY = "k0ioWF4va2wnM"
    CLICK_MERCHANT_USER_ID = "77105"
    
    # ТЕСТОВЫЕ КЛЮЧИ (Для боевого режима поменяй URL и KEY)
    PAYME_KEY = "tdG7P3KSJ1BKKbsB%HQYkU4i4C35RnbHcIao"
    PAYME_URL = "https://checkout.test.paycom.uz" 
    
    # PAYME_KEY = "&INTUFcIEXtRIBYmKRs21Ep2GyI30AKEK4#C"
    # PAYME_URL = "https://checkout.paycom.uz"

    PAYME_ACCOUNT_FIELD = "order_id"
    PAYME_MIN_AMOUNT = 100000 
    ORDER_PAYMENT_TIMEOUT_MINUTES = 20
    MIN_ORDER_AMOUNT = 100
    # Код упаковки по умолчанию (без упаковки). При необходимости заменить на код из ОФД.
    DEFAULT_PACKAGE_CODE = "000000"

    @property
    def DATABASE_URL(self):
        return f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASS}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"

settings = Settings()
