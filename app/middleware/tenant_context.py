# app/middleware/tenant_context.py
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Set
from uuid import UUID, ValueError as UUIDValueError
import logging
import re

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le contexte tenant dans les requêtes"""
    
    # Chemins exacts exclus
    EXACT_EXCLUDED_PATHS: Set[str] = {
        "/",
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
    
    # Patterns de chemins exclus (commencent par)
    PREFIX_EXCLUDED_PATHS: Set[str] = {
        "/auth/login",
        "/auth/tenants/register",
        "/auth/verify-sms",
        "/auth/resend-sms",
        "/auth/password/reset/request",
        "/auth/password/reset/confirm",
        "/auth/activation-status/",
        "/auth/health",
        "/auth/api-status",
    }
    
    def _is_path_excluded(self, path: str) -> bool:
        """Vérifie si le chemin est exclu de la vérification tenant"""
        # Vérification exacte
        if path in self.EXACT_EXCLUDED_PATHS:
            return True
        
        # Vérification par préfixe
        for excluded_prefix in self.PREFIX_EXCLUDED_PATHS:
            if path.startswith(excluded_prefix):
                return True
        
        return False
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Routes exclues - pas de vérification tenant
        if self._is_path_excluded(path):
            logger.debug(f"Route exclue de la vérification tenant: {path}")
            return await call_next(request)
        
        # Récupération du tenant ID
        tenant_id = self._extract_tenant_id(request)
        
        # Si pas de tenant ID, erreur 400
        if not tenant_id:
            logger.warning(f"Requête sans tenant ID: {request.method} {path}")
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "TENANT_ID_REQUIRED",
                    "message": "Tenant ID manquant",
                    "hint": "Ajoutez l'en-tête 'X-Tenant-ID' ou le paramètre 'tenant_id'"
                },
            )
        
        # Validation du format UUID
        tenant_uuid = self._validate_tenant_id(tenant_id, path)
        if tenant_uuid is None:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "INVALID_TENANT_ID",
                    "message": "Tenant ID invalide",
                    "hint": "Le tenant ID doit être un UUID valide"
                },
            )
        
        # Stockage dans l'état de la requête
        request.state.tenant_id = tenant_uuid
        
        # Exécution de la requête avec gestion d'erreur propre
        try:
            response = await call_next(request)
            response.headers["X-Tenant-ID"] = str(tenant_uuid)
            return response
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la requête: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "INTERNAL_SERVER_ERROR",
                    "message": "Erreur interne du serveur"
                },
            )
    
    def _extract_tenant_id(self, request: Request) -> Optional[str]:
        """Extrait le tenant ID des headers ou query params"""
        # Priorité au header
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return tenant_id.strip()
        
        # Fallback sur query param
        tenant_id = request.query_params.get("tenant_id")
        if tenant_id:
            return tenant_id.strip()
        
        # Fallback sur sous-domaine (optionnel)
        host = request.headers.get("host", "")
        if "." in host:
            subdomain = host.split(".")[0]
            if subdomain and subdomain not in {"www", "app", "api", "localhost"}:
                return subdomain
        
        return None
    
    def _validate_tenant_id(self, tenant_id: str, path: str) -> Optional[UUID]:
        """Valide le format du tenant ID"""
        try:
            return UUID(tenant_id)
        except UUIDValueError:
            logger.warning(f"Tenant ID invalide: '{tenant_id}' pour {path}")
            return None