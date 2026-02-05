import sys
from loguru import logger
from app.config import settings

# Configure logger
logger.remove() # Remove default handler
logger.add(sys.stdout, level="INFO")
logger.add("logs/app.log", rotation="500 MB", level="DEBUG", compression="zip")

__all__ = ["logger"]
