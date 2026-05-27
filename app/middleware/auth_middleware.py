from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.security import decode_token

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Initialiser request.state.user à None par défaut
        request.state.user = None
        
        # Récupérer le token depuis les headers
        auth_header = request.headers.get("Authorization")
        
        if auth_header and auth_header.startswith("Bearer "):
            try:
                token = auth_header.replace("Bearer ", "")
                
                # Décoder le token
                payload = decode_token(token)
                
                # Vérifier que c'est un token d'accès valide
                token_type = payload.get("type")
                if token_type == "access":
                    request.state.user = payload
                    
            except Exception:
                # En cas d'erreur de décodage, on laisse user = None
                # L'authentification sera vérifiée plus tard par les dépendances
                pass
        
        # Continuer avec la requête
        response = await call_next(request)
        return response

UnifiedAuthSubscriptionMiddleware = AuthMiddleware