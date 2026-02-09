import re
from typing import List, Optional, Literal
from fastapi import Form, HTTPException
from pydantic import BaseModel, Field, ValidationError, validator
from .base import FormSchema

class OrderCreateSchema(FormSchema):
    item_ids: List[int]
    delivery_method: Literal["pickup", "delivery"]
    payment_method: Literal["cash", "card", "click"]
    phone: str
    address: Optional[str] = Field(None, max_length=500)
    comment: Optional[str] = Field(None, max_length=500)

    @validator("phone")
    def validate_phone(cls, v):
        # Remove +, parentheses, spaces, hyphens
        v = re.sub(r'[^\d]', '', v)
        
        # Автоматически добавляем 998 если введены только 9 цифр
        if len(v) == 9:
            v = "998" + v

        if len(v) == 12 and v.startswith("998"):
            return v

        raise ValueError("Неверный формат телефона. Введите номер в формате 998XXXXXXXXX или XXXXXXXXX")

    @validator("address")
    def validate_address(cls, v, values):
        method = values.get("delivery_method")
        if method == "delivery" and not v:
            raise ValueError("Адрес обязателен для доставки")
        return v

    @classmethod
    def as_form(
        cls,
        item_ids: List[int] = Form(...),
        delivery_method: str = Form(..., pattern="^(pickup|delivery)$"),
        payment_method: str = Form(..., pattern="^(cash|card|click)$"),
        phone: str = Form(...),
        address: Optional[str] = Form(None),
        comment: Optional[str] = Form(None),
    ):
        try:
            return cls(
                item_ids=item_ids,
                delivery_method=delivery_method,
                payment_method=payment_method,
                phone=phone,
                address=address,
                comment=comment
            )
        except ValidationError as exc:
            errors = [f"{err['loc'][0]}: {err['msg']}" for err in exc.errors()]
            raise HTTPException(status_code=422, detail="; ".join(errors))
