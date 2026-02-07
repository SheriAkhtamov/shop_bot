from typing import Optional
from fastapi import Form, UploadFile, File
from pydantic import BaseModel, Field
from .base import FormSchema
from app.config import settings

class ProductCreateSchema(FormSchema):
    name_ru: str = Field(..., min_length=2)
    name_uz: str = Field(..., min_length=2)
    category_id: int
    price: int = Field(..., gt=0)
    stock: int = Field(..., ge=0)
    description_ru: Optional[str] = ""
    description_uz: Optional[str] = ""
    ikpu: Optional[str] = "00702001001000001"
    package_code: Optional[str] = settings.DEFAULT_PACKAGE_CODE
    
    # Image is handled separately via File(), but we can include it in the model info if needed.
    # For now, we keep it separate in the Depends because it's UploadFile
    
    @classmethod
    def as_form(
        cls,
        name_ru: str = Form(...),
        name_uz: str = Form(...),
        category_id: int = Form(...),
        price: int = Form(...),
        stock: int = Form(...),
        description_ru: str = Form(""),
        description_uz: str = Form(""),
        ikpu: str = Form("00702001001000001"),
        package_code: str = Form(settings.DEFAULT_PACKAGE_CODE),
    ):
        return cls(
            name_ru=name_ru,
            name_uz=name_uz,
            category_id=category_id,
            price=price,
            stock=stock,
            description_ru=description_ru,
            description_uz=description_uz,
            ikpu=ikpu,
            package_code=package_code
        )
