from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.audit_service import log_action
from app.db.session import get_db
import logging

logger = logging.getLogger(__name__)

class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Exécuter la requête d'abord
        response = await call_next(request)
        
        # 2. Ne pas loguer les méthodes de lecture (GET) pour éviter de saturer la DB
        # Supprime cette condition si tu veux absolument TOUT loguer
        if request.method == "GET":
            return response

        try:
            # Récupérer l'utilisateur depuis request.state (injecté par ton AuthMiddleware)
            user_payload = getattr(request.state, "user", None)
            
            if not user_payload:
                return response
                
            user_id = user_payload.get("sub")
            tenant_id = user_payload.get("tenant_id")
            
            # Gestion propre de la session DB
            db = next(get_db())
            try:
                # --- CORRECTION MAJEURE : Pas de 'await' ici car log_action est synchrone ---
                log_action(
                    db=db,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    action=request.method,
                    cible=self._get_entity_from_path(request.url.path),
                    description=f"{request.method} {request.url.path} - Status: {response.status_code}",
                    status=response.status_code, # Sera mappé en status_code par le service
                    user_agent=request.headers.get("user-agent"),
                    ip=request.client.host if request.client else None,
                    details={
                        "path": request.url.path,
                        "query_params": str(request.query_params)
                    }
                )
                db.commit() # On commit le log d'audit
            except Exception as e:
                db.rollback()
                logger.error(f"Audit log database error: {str(e)}")
            finally:
                db.close()
            
        except Exception as e:
            # Sécurité ultime : le middleware ne doit JAMAIS faire planter l'API
            logger.error(f"Middleware Audit Error: {str(e)}")
        
        return response

    def _get_entity_from_path(self, path: str) -> str:
        """Extrait le nom de l'entité depuis l'URL (ex: /api/v1/inventory/alerts -> inventory)"""
        parts = path.split('/')
        # Cherche la partie après v1
        if "v1" in parts:
            idx = parts.index("v1")
            if len(parts) > idx + 1:
                return parts[idx+1]
        return "system"