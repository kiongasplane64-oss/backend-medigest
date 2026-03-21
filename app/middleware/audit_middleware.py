from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.audit_service import log_action
from app.db.session import get_db
from app.core.constants import SYSTEM_TENANT_ID  
import logging

logger = logging.getLogger(__name__)

class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Exécuter la requête d'abord
        response = await call_next(request)
        
        # 2. Ne pas loguer les méthodes de lecture (GET) pour éviter de saturer la DB
        # Optionnel : vous pouvez aussi ignorer OPTIONS et HEAD
        if request.method in ["GET", "OPTIONS", "HEAD"]:
            return response

        try:
            # Récupérer l'utilisateur depuis request.state (injecté par ton AuthMiddleware)
            user_payload = getattr(request.state, "user", None)
            
            if not user_payload:
                return response
                
            user_id = user_payload.get("sub")
            tenant_id = user_payload.get("tenant_id")
            user_role = user_payload.get("role")
            
            # 🔥 CORRECTION CRITIQUE 🔥
            # Pour les super admins (sans tenant) qui font des actions globales,
            # on utilise le tenant système
            if tenant_id is None and user_role in ["super_admin", "superadmin"]:
                tenant_id = SYSTEM_TENANT_ID
                logger.debug(f"Using SYSTEM_TENANT_ID for super admin action: {request.method} {request.url.path}")
            
            # Pour les autres utilisateurs, si tenant_id est None, on logge quand même
            # avec SYSTEM_TENANT_ID pour ne pas casser la contrainte NOT NULL
            if tenant_id is None:
                tenant_id = SYSTEM_TENANT_ID
                logger.warning(f"No tenant_id found for user {user_id}, using SYSTEM_TENANT_ID")
            
            # Gestion propre de la session DB
            db = next(get_db())
            try:
                # Déterminer l'action en fonction du rôle et du contexte
                action = self._determine_action(request.method, user_role, request.url.path)
                
                # Log de l'action
                log_action(
                    db=db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action=action,
                    cible=self._get_entity_from_path(request.url.path),
                    description=f"{request.method} {request.url.path} - Status: {response.status_code}",
                    status=response.status_code,
                    user_agent=request.headers.get("user-agent"),
                    ip=request.client.host if request.client else None,
                    details={
                        "path": request.url.path,
                        "query_params": str(request.query_params),
                        "user_role": user_role,
                        "method": request.method,
                        "status_code": response.status_code
                    }
                )
                db.commit()
            except Exception as e:
                db.rollback()
                logger.error(f"Audit log database error: {str(e)}")
            finally:
                db.close()
            
        except Exception as e:
            # Sécurité ultime : le middleware ne doit JAMAIS faire planter l'API
            logger.error(f"Middleware Audit Error: {str(e)}")
        
        return response

    def _determine_action(self, method: str, role: str, path: str) -> str:
        """Détermine une action plus descriptive que juste la méthode HTTP"""
        
        # Actions spécifiques pour les super admins
        if role in ["super_admin", "superadmin"]:
            if "dashboard" in path:
                return "VIEW_DASHBOARD"
            elif "tenants" in path and method == "POST":
                return "SUPER_ADMIN_CREATE_TENANT"
            elif "tenants" in path and method == "PUT":
                return "SUPER_ADMIN_UPDATE_TENANT"
            elif "tenants" in path and "actions" in path:
                return "SUPER_ADMIN_TENANT_ACTION"
            elif "system" in path:
                return "SYSTEM_CONFIGURATION"
            elif "users" in path and "super-admins" in path:
                return "CREATE_SUPER_ADMIN"
            elif "subscriptions" in path:
                return "VIEW_SUBSCRIPTIONS"
            elif "analytics" in path:
                return "VIEW_ANALYTICS"
        
        # Actions spécifiques pour les admins normaux
        elif role == "admin":
            if "users" in path and method == "POST":
                return "CREATE_USER"
            elif "users" in path and method == "PUT":
                return "UPDATE_USER"
            elif "users" in path and method == "DELETE":
                return "DELETE_USER"
            elif "products" in path and method == "POST":
                return "CREATE_PRODUCT"
            elif "products" in path and method == "PUT":
                return "UPDATE_PRODUCT"
            elif "sales" in path and method == "POST":
                return "CREATE_SALE"
        
        # Actions standard
        action_map = {
            "POST": "CREATE",
            "PUT": "UPDATE",
            "PATCH": "UPDATE",
            "DELETE": "DELETE",
            "OPTIONS": "OPTIONS"
        }
        
        return action_map.get(method, method)

    def _get_entity_from_path(self, path: str) -> str:
        """Extrait le nom de l'entité depuis l'URL (ex: /api/v1/inventory/alerts -> inventory)"""
        parts = path.split('/')
        # Cherche la partie après v1
        if "v1" in parts:
            idx = parts.index("v1")
            if len(parts) > idx + 1:
                entity = parts[idx+1]
                # Nettoyer l'entité (enlever les paramètres)
                if '?' in entity:
                    entity = entity.split('?')[0]
                return entity
        return "system"