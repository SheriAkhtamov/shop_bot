import base64
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_200_OK

from sqlalchemy.ext.asyncio import AsyncSession
from app.database.core import get_db
from app.config import settings
from app.services.payme_logic import PaymeService, PaymeException, PaymeErrors

router = APIRouter(prefix="/api/payme", tags=["payme"])

# Настраиваем логирование, чтобы видеть запросы от Payme
logger = logging.getLogger("payme")

# --- ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: Генерация ссылки ---
# Вынесена в app/utils/payment.py



# --- WEBHOOK: Обработка запросов от Payme ---

@router.post("")
async def payme_webhook(
    request: Request,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_db)
):
    """
    Единая точка входа для всех запросов Payme.
    """
    
    # 1. Читаем тело запроса
    try:
        body = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": PaymeErrors.JSON_PARSE_ERROR, "message": "Invalid JSON"}}

    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    # 2. Проверка Авторизации (Basic Auth)
    # Payme шлет заголовок: Authorization: Basic base64("PaymeBusiness:KEY")
    if not authorization:
         return response_error(request_id, PaymeErrors.INSUFFICIENT_PRIVILEGE, "Auth required")
    
    try:
        scheme, credentials = authorization.split()
        if scheme.lower() != 'basic':
            raise ValueError
        decoded = base64.b64decode(credentials).decode("utf-8")
        login, password = decoded.split(":", 1) # Ограничиваем split чтобы двоеточие в пароле не ломало парсинг
        
        # Проверяем пароль (Ключ из настроек)
        # Логин PaymeBusiness игнорируем или проверяем, если нужно
        if password != settings.PAYME_KEY:
             return response_error(request_id, PaymeErrors.INSUFFICIENT_PRIVILEGE, "Wrong password")
             
    except Exception:
        return response_error(request_id, PaymeErrors.INSUFFICIENT_PRIVILEGE, "Auth invalid")

    # 3. Маршрутизация методов
    service = PaymeService(session)
    result = None
    
    try:
        if method == "CheckPerformTransaction":
            result = await service.check_perform_transaction(params.get("amount"), params.get("account"))
            
        elif method == "CreateTransaction":
            result = await service.create_transaction(
                params.get("id"), 
                params.get("time"), 
                params.get("amount"), 
                params.get("account")
            )
            
        elif method == "PerformTransaction":
            result = await service.perform_transaction(params.get("id"))
            
        elif method == "CancelTransaction":
            result = await service.cancel_transaction(params.get("id"), params.get("reason"))
            
        elif method == "CheckTransaction":
            result = await service.check_transaction(params.get("id"))
            
        elif method == "GetStatement":
            result = await service.get_statement(params.get("from"), params.get("to")) 
            
        elif method == "ChangePassword":
            result = {"success": True} # Просто подтверждаем смену пароля (если инициировано из кабинета)
            
        else:
            return response_error(request_id, PaymeErrors.METHOD_NOT_FOUND, f"Method {method} not found")

    except PaymeException as e:
        return response_error(request_id, e.code, e.message, e.data)
    except Exception as e:
        logger.error(f"Payme System Error: {e}")
        return response_error(request_id, -32400, "System Error")

    # 4. Успешный ответ
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    }

def response_error(req_id, code, message, data=None):
    """Формирует JSON-RPC ошибку"""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": code,
            "message": {"ru": str(message)} if isinstance(message, str) else message,
            "data": data
        }
    }
