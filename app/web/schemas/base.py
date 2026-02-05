from typing import Type, TypeVar, Any
from fastapi import Form, UploadFile, File
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

class FormSchema(BaseModel):
    @classmethod
    def as_form(cls, *args, **kwargs) -> Any:
        # This is a placeholder. In reality, we need to explicitly define the signature 
        # for FastAPI to recognize the Form parameters.
        # Since we can't easily dynamically generate signature with correct types for FastAPI dependency injection 
        # without complex metaprogramming that might confuse linters,
        # we will define specific `as_form` class methods in the subclasses.
        pass
