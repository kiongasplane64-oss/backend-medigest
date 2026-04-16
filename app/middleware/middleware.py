# app/core/middleware.py - 

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.api.v1.auth import is_subscription_active
import logging

logger = logging.getLogger(__name__)

class SubscriptionCheckMiddleware(BaseHTTPMiddleware):
    """Middleware pour vérifier l'abonnement sur les endpoints protégés"""
    
    # Endpoints exemptés de la vérification
    EXEMPT_PATHS = [
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/tenants/register",
        "/api/v1/auth/password/reset",
        "/api/v1/auth/health",
        "/api/v1/auth/api-status",
        "/api/v1/auth/verify-subscription",  # Endpoint de vérification lui-même
        "/docs",
        "/redoc",
        "/openapi.json"
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Vérifier si le chemin est exempté
        path = request.url.path
        if any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS):
            return await call_next(request)
        
        # Récupérer le token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return await call_next(request)
        
        token = auth_header.replace("Bearer ", "")
        
        # Décoder le token pour obtenir le tenant_id
        try:
            from jose import jwt
            from app.core.config import settings
            
            payload = jwt.decode(
                token, 
                settings.SECRET_KEY, 
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": True}  # Vérifier l'expiration
            )
            
            tenant_id = payload.get("tenant_id")
            user_id = payload.get("sub")
            
            if tenant_id and user_id:
                # Vérifier l'abonnement dans la base de données
                db = SessionLocal()
                try:
                    subscription_active = is_subscription_active(db, tenant_id)
                    
                    if not subscription_active:
                        logger.warning(f"Abonnement expiré pour tenant {tenant_id}, utilisateur {user_id}")
                        return JSONResponse(
                            status_code=403,
                            content={
                                "detail": {
                                    "error": "subscription_expired",
                                    "message": "Votre abonnement a expiré. Veuillez renouveler votre abonnement.",
                                    "requires_relogin": True
                                }
                            }
                        )
                finally:
                    db.close()
                    
        except jwt.ExpiredSignatureError:
            # Token expiré - laisser passer pour que le client refresh
            pass
        except Exception as e:
            logger.error(f"Erreur dans middleware d'abonnement: {e}")
        
        return await call_next(request)