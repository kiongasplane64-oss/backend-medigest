#app/core/secrets.py
import os
from base64 import urlsafe_b64encode

def get_secret_key() -> bytes:
    key = os.getenv("APP_SECRET_KEY")
    if not key:
        raise RuntimeError("APP_SECRET_KEY manquant")
    return urlsafe_b64encode(key.encode())
