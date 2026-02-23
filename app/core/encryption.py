#app/core/encryption.py
from cryptography.fernet import Fernet
from app.core.secrets import get_secret_key

fernet = Fernet(get_secret_key())

def encrypt_value(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()

def decrypt_value(value: str) -> str:
    return fernet.decrypt(value.encode()).decode()
