import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from passlib.context import CryptContext
from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def check_telegram_auth(init_data: str) -> dict | None:
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return None
        
        hash_check = parsed_data.pop("hash")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", settings.BOT_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash == hash_check:
            # Check auth_date for replay attacks (24 hours)
            if "auth_date" in parsed_data:
                import time
                auth_date = int(parsed_data["auth_date"])
                if time.time() - auth_date > 86400:
                    return None

            user_data = parsed_data.get("user")
            if user_data:
                return json.loads(user_data)
        return None
    except Exception:
        return None