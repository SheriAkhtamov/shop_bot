import asyncio
import os
from app.utils.logger import logger

async def delete_file(file_path: str):
    """
    Удаляет файл с диска.
    :param file_path: Путь к файлу (относительный или абсолютный)
    """
    if not file_path:
        return
        
    # Игнорируем дефолтные изображения (если они есть)
    if "no-image" in file_path or "default" in file_path:
        return

    try:
        # Убираем начальный слеш, если путь от корня
        clean_path = file_path.lstrip('/')
        clean_path = clean_path.lstrip('\\')
        
        if await asyncio.to_thread(os.path.exists, clean_path):
            await asyncio.to_thread(os.remove, clean_path)
            logger.info(f"File deleted: {clean_path}")
        else:
            logger.warning(f"File not found for deletion: {clean_path}")

    except Exception as e:
        logger.error(f"Error deleting file {file_path}: {e}")
