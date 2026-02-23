# app/middleware/tenant_context.py
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le contexte tenant dans les requêtes"""
    
    # Liste des chemins qui n'ont pas besoin de tenant ID
    EXCLUDED_PATHS = [
        "/",  # Route racine
        "/health",  # Health check
        "/docs",  # Documentation Swagger
        "/redoc",  # Documentation Redoc
        "/openapi.json",  # Schema OpenAPI
        # Routes d'authentification
        "/auth/login",
        "/auth/tenants/register",
        "/auth/verify-sms",
        "/auth/resend-sms",
        "/auth/password/reset/request",
        "/auth/password/reset/confirm",
        # Pattern pour /auth/activation-status/{email}
        "/auth/activation-status/",
    ]
    
    async def dispatch(self, request: Request, call_next):
        try:
            path = request.url.path
            
            # Vérifier si le chemin est exclu
            is_excluded = False
            for excluded_path in self.EXCLUDED_PATHS:
                if path == excluded_path or path.startswith(excluded_path):
                    is_excluded = True
                    break
            
            if is_excluded:
                logger.debug(f"Route exclue de la vérification tenant: {path}")
                return await call_next(request)
            
            # Récupération du tenant ID depuis les headers
            tenant_id = request.headers.get("X-Tenant-ID")
            
            # Récupération alternative depuis les query params (optionnel)
            if not tenant_id:
                tenant_id = request.query_params.get("tenant_id")
            
            # Si aucun tenant_id n'est trouvé, on renvoie une erreur
            if not tenant_id:
                logger.warning(f"Requête sans tenant ID: {request.method} {path}")
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": "Tenant ID manquant",
                        "hint": "Ajoutez l'en-tête 'X-Tenant-ID' ou le paramètre 'tenant_id'"
                    },
                )
            
            # Validation du tenant ID (doit être un UUID valide)
            try:
                from uuid import UUID
                tenant_uuid = UUID(tenant_id)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": "Tenant ID invalide",
                        "hint": "Le tenant ID doit être un UUID valide"
                    },
                )
            
            # Stockage du tenant_id dans l'état de la requête
            request.state.tenant_id = tenant_uuid
            
            # Ajout d'en-têtes de réponse pour le debug
            response = await call_next(request)
            
            # Ajout du tenant_id dans les en-têtes de réponse pour le tracking
            response.headers["X-Tenant-ID"] = str(tenant_uuid)
            
            return response
            
        except Exception as e:
            logger.error(f"Erreur dans TenantContextMiddleware: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Erreur interne du serveur",
                    "error": str(e)
                },
            )