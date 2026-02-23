# app/core/config.py
import os
from datetime import timedelta
from typing import Optional, List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration de l'application avec validation Pydantic"""
    
    # =====================================
    # APPLICATION
    # =====================================
    APP_NAME: str = "MEDIGEST PRO"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True
    API_V1_STR: str = "/api/v1"
    
    # CORRECTION : Ajout de l'annotation de type ': str'
    INITIAL_SETUP_KEY: str = os.getenv("INITIAL_SETUP_KEY", "MaCleSuperSecrete123!")
    
    # =====================================
    # SÉCURITÉ JWT
    # =====================================
    SECRET_KEY: str = "azJ9HfksZRmhOGh5Q0qMOwK81hhoY7UWFFDrK5_Nevw"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # =====================================
    # BASE DE DONNÉES
    # =====================================
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "postgres")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "pharma_saas")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: str = os.getenv("POSTGRES_PORT", "5432")
    
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )
    
    # =====================================
    # SQLALCHEMY CONFIGURATION
    # =====================================
    SQLALCHEMY_ECHO: bool = False
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    
    @property
    def SQLALCHEMY_ENGINE_OPTIONS(self) -> dict:
        return {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_timeout": 30,
            "pool_recycle": 1800,
            "pool_pre_ping": True,
            "connect_args": {"client_encoding": "utf8", "connect_timeout": 10}
        }
    
    # =====================================
    # CORS
    # =====================================
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    
    # =====================================
    # SAAS CONFIGURATION
    # =====================================
    DEFAULT_CURRENCY: str = "CDF"
    DEFAULT_LANGUAGE: str = "fr"
    DEFAULT_TIMEZONE: str = "Africa/Kinshasa"
    
    # =====================================
    # FILES & UPLOADS
    # =====================================
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024
    ALLOWED_EXTENSIONS: List[str] = [".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx"]
    
    # =====================================
    # EMAIL
    # =====================================
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: Optional[int] = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    EMAIL_FROM: str = "noreply@medigest.com"

    # =====================================
    # SMS & WHATSAPP CONFIGURATION
    # =====================================
    SMS_ENABLED: bool = True
    WHATSAPP_ENABLED: bool = True
    SMS_CODE_EXPIRATION_MINUTES: int = 5
    MAX_SMS_ATTEMPTS: int = 3

    # =====================================
    # TWILIO
    # =====================================
    TWILIO_SID: str = os.getenv("TWILIO_SID", "")
    TWILIO_AUTH_TOKEN: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_PHONE_NUMBER: str = os.getenv("TWILIO_PHONE_NUMBER", "")
    TWILIO_WHATSAPP_NUMBER: str = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
    TWILIO_LOOKUP_ENABLED: bool = False 

    # =====================================
    # LOGGING
    # =====================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore" # Permet d'ignorer les variables en trop dans le .env sans planter
    }


# Instance globale des paramètres
settings = Settings()