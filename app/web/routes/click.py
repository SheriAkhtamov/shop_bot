from fastapi import APIRouter, Request, Depends, Form
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.core import get_db
from app.services.click_logic import ClickService

router = APIRouter(prefix="/api/click", tags=["click"])

@router.post("/prepare")
async def click_prepare(
    click_trans_id: int = Form(...),
    service_id: int = Form(...),
    click_paydoc_id: int = Form(...),
    merchant_trans_id: str = Form(...),
    amount: str = Form(...),
    action: int = Form(...),
    error: int = Form(0),
    error_note: str = Form(""),
    sign_time: str = Form(...),
    sign_string: str = Form(...),
    session: AsyncSession = Depends(get_db)
):
    """
    URL для метода Prepare (проверка)
    В настройках Click указать: https://unicombot.uz/api/click/prepare
    """
    data = locals() # Собираем все аргументы в словарь
    del data['session'] # Убираем сессию из данных
    
    service = ClickService(session)
    return await service.prepare(data)

@router.post("/complete")
async def click_complete(
    click_trans_id: int = Form(...),
    service_id: int = Form(...),
    click_paydoc_id: int = Form(...),
    merchant_trans_id: str = Form(...),
    amount: str = Form(...),
    action: int = Form(...),
    error: int = Form(0),
    error_note: str = Form(""),
    sign_time: str = Form(...),
    sign_string: str = Form(...),
    session: AsyncSession = Depends(get_db)
):
    """
    URL для метода Complete (оплата)
    В настройках Click указать: https://unicombot.uz/api/click/complete
    """
    data = locals()
    del data['session']
    
    service = ClickService(session)
    return await service.complete(data)
