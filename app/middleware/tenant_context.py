# app/middleware/tenant_context.py - Version complète avec toutes les routes exclues

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Optional, Set
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Middleware pour gérer le contexte tenant dans les requêtes"""
    
    def __init__(self, app):
        super().__init__(app)
        
        # Chemins exacts exclus
        self.EXACT_EXCLUDED_PATHS: Set[str] = {
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        }
        
        # Patterns de chemins exclus (commencent par)
        self.PREFIX_EXCLUDED_PATHS: Set[str] = {
            # ============================================
            # ROUTES D'AUTHENTIFICATION
            # ============================================
            "/auth/login",
            "/auth/tenants/register",
            "/auth/verify-sms",
            "/auth/resend-sms",
            "/auth/password/reset/request",
            "/auth/password/reset/confirm",
            "/auth/activation-status/",
            "/auth/health",
            "/auth/api-status",
            
            "/api/v1/auth/login",
            "/api/v1/auth/tenants/register",
            "/api/v1/auth/verify-sms",
            "/api/v1/auth/resend-sms",
            "/api/v1/auth/password/reset/request",
            "/api/v1/auth/password/reset/confirm",
            "/api/v1/auth/activation-status/",
            "/api/v1/auth/health",
            "/api/v1/auth/api-status",
            "/api/v1/auth/me",  # ⭐ AJOUTÉ
            
            # ============================================
            # ROUTES UTILISATEURS
            # ============================================
            "/api/v1/users/me",  # ⭐ AJOUTÉ
            
            # ============================================
            # ROUTES BRANCHES
            # ============================================
            "/api/v1/branches/current",  # ⭐ AJOUTÉ
            "/api/v1/sync/user/branch",  # ⭐ AJOUTÉ
            
            # ============================================
            # ROUTES STOCK - IMPORTANT pour les requêtes individuelles
            # ============================================
            "/api/v1/stock/",  # ⭐ AJOUTÉ - pour les requêtes GET /api/v1/stock/{id}
            
            # ============================================
            # ROUTES SYNCHRONISATION
            # ============================================
            "/api/v1/sync/",
            
            # ============================================
            # WEBHOOKS ET CALLBACKS
            # ============================================
            "/webhooks/",
            "/payments/webhook/",
            "/api/v1/payments/webhook/",
            
            # ============================================
            # ROUTES PUBLIQUES
            # ============================================
            "/api/v1/public/",
            "/public/",
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
        
        # Récupération du tenant ID ou branch_id
        tenant_id = self._extract_tenant_id(request)
        branch_id = self._extract_branch_id(request)
        
        # ✅ Si branch_id est présent, on peut continuer sans tenant
        if branch_id:
            logger.debug(f"Requête avec branch_id: {branch_id} pour {path}")
            request.state.branch_id = branch_id
            request.state.tenant_id = None
            
            try:
                response = await call_next(request)
                if branch_id:
                    response.headers["X-Branch-ID"] = str(branch_id)
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
        
        # Si pas de tenant ID, on laisse passer certaines routes protégées par token
        if not tenant_id:
            # Vérifier si la route nécessite une authentification par token
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                # Les routes avec token peuvent passer sans tenant_id
                # Le tenant sera déduit du token
                logger.debug(f"Route avec token mais sans tenant: {path}")
                request.state.tenant_id = None
                request.state.branch_id = None
                
                try:
                    response = await call_next(request)
                    return response
                except Exception as e:
                    logger.error(f"Erreur lors du traitement: {str(e)}")
                    return JSONResponse(
                        status_code=500,
                        content={
                            "success": False,
                            "error": "INTERNAL_SERVER_ERROR",
                            "message": "Erreur interne du serveur"
                        },
                    )
            
            logger.warning(f"Requête sans tenant ID ni branch_id et sans token: {request.method} {path}")
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "TENANT_OR_BRANCH_REQUIRED",
                    "message": "Tenant ID ou Branch ID manquant",
                    "hint": "Ajoutez l'en-tête 'X-Tenant-ID', 'X-Branch-ID', ou les paramètres 'tenant_id'/'branch_id'"
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
        request.state.branch_id = None
        
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
    
    def _extract_branch_id(self, request: Request) -> Optional[str]:
        """Extrait le branch ID des headers ou query params"""
        # Priorité au header
        branch_id = request.headers.get("X-Branch-ID")
        if branch_id:
            return branch_id.strip()
        
        # Fallback sur query param
        branch_id = request.query_params.get("branch_id")
        if branch_id:
            return branch_id.strip()
        
        return None
    
    def _validate_tenant_id(self, tenant_id: str, path: str) -> Optional[UUID]:
        """Valide le format du tenant ID"""
        try:
            return UUID(tenant_id)
        except ValueError:
            logger.warning(f"Tenant ID invalide: '{tenant_id}' pour {path}")
            return None