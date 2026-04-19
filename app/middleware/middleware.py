# app/middleware/middleware.py

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from app.db.session import SessionLocal
from app.api.v1.auth import is_subscription_active
import logging
import re

logger = logging.getLogger(__name__)


class SubscriptionMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le mode lecture seule sur abonnement expiré"""
    
    # Méthodes autorisées en lecture seule
    READ_ONLY_METHODS = {'GET', 'OPTIONS', 'HEAD'}
    
    # Endpoints toujours autorisés (même en écriture)
    ALWAYS_ALLOWED_PATHS = [
        r'^/api/v1/auth/.*$',
        r'^/api/v1/subscriptions/.*$',
        r'^/api/v1/health$',
        r'^/api/v1/me$',
        r'^/api/v1/tenants/me$',
        r'^/api/v1/session/.*$',
        r'^/api/v1/pharmacies/.*/service-status$',
        r'^/api/v1/subscriptions/status$',
        r'^/api/v1/subscriptions/usage$',
        r'^/api/v1/subscriptions/plans$',
        r'^/api/v1/stock/alerts/.*$',  # Lecture des alertes OK
        r'^/api/v1/dashboard/.*$',      # Dashboard en lecture OK
        r'^/docs$',
        r'^/redoc$',
        r'^/openapi.json$'
    ]
    
    async def dispatch(self, request: Request, call_next):
        # Récupérer l'utilisateur depuis le token (déjà décodé par le dépendance)
        user = getattr(request.state, 'user', None)
        
        if user and user.tenant_id:
            db = SessionLocal()
            try:
                # Vérifier l'abonnement
                subscription_active = is_subscription_active(db, str(user.tenant_id))
                
                if not subscription_active:
                    method = request.method
                    path = request.url.path
                    
                    # Vérifier si le chemin est toujours autorisé
                    is_allowed_endpoint = any(
                        re.match(pattern, path) for pattern in self.ALWAYS_ALLOWED_PATHS
                    )
                    
                    # Si méthode non lecture seule et pas endpoint spécial -> bloquer
                    if method not in self.READ_ONLY_METHODS and not is_allowed_endpoint:
                        logger.warning(
                            f"🔒 Tentative d'écriture en mode lecture seule - "
                            f"User: {user.email}, Method: {method}, Path: {path}"
                        )
                        return JSONResponse(
                            status_code=403,
                            content={
                                "error": "subscription_expired_readonly",
                                "message": "Votre abonnement a expiré. Mode lecture seule uniquement.",
                                "read_only_mode": True,
                                "subscription_expired": True,
                                "action": "Renouvelez votre abonnement pour modifier vos données",
                                "allowed_operations": list(self.READ_ONLY_METHODS),
                                "forbidden_operations": ["POST", "PUT", "PATCH", "DELETE"],
                                "renewal_url": "/api/v1/subscriptions/plans"
                            }
                        )
                    
                    # Ajouter un flag pour le mode lecture seule
                    request.state.read_only_mode = True
                    logger.info(f"📖 Mode lecture seule activé pour {user.email}")
                    
            finally:
                db.close()
        
        # Traiter la requête
        response = await call_next(request)
        
        # Ajouter les headers d'information
        if hasattr(request.state, 'read_only_mode') and request.state.read_only_mode:
            response.headers["X-Read-Only-Mode"] = "true"
            response.headers["X-Subscription-Expired"] = "true"
            response.headers["X-Allowed-Methods"] = ", ".join(self.READ_ONLY_METHODS)
        
        return response