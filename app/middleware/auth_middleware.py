# auth_middleware.py - Version corrigée

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import logging
from typing import Optional

# Importer votre fonction decode_token
from app.core.security import decode_token

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware d'authentification pour FastAPI.
    Extrait et valide le token JWT des requêtes entrantes.
    """
    
    async def dispatch(self, request: Request, call_next):
        """
        Traite chaque requête et ajoute l'utilisateur au request.state
        """
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
                if payload and isinstance(payload, dict):
                    token_type = payload.get("type")
                    if token_type == "access":
                        request.state.user = payload
                        logger.debug(f"Utilisateur authentifié: {payload.get('sub')}")
                        
            except Exception as e:
                logger.warning(f"Erreur d'authentification: {e}")
                # On laisse user = None, la requête continue
                # Les endpoints protégés vérifieront via les dépendances
        
        # Continuer avec la requête
        response = await call_next(request)
        return response


class OptionalAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware d'authentification optionnelle.
    N'émet aucune erreur si le token est invalide ou absent.
    """
    
    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        
        auth_header = request.headers.get("Authorization")
        
        if auth_header and auth_header.startswith("Bearer "):
            try:
                token = auth_header.replace("Bearer ", "")
                payload = decode_token(token)
                
                if payload and isinstance(payload, dict):
                    token_type = payload.get("type")
                    if token_type == "access":
                        request.state.user = payload
                        
            except Exception as e:
                logger.debug(f"Authentification optionnelle ignorée: {e}")
        
        response = await call_next(request)
        return response


class RequiredAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware d'authentification obligatoire.
    Retourne 401 si le token est invalide ou absent.
    """
    
    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("Authorization")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            return Response(
                content='{"detail": "Authentification requise"}',
                status_code=401,
                media_type="application/json"
            )
        
        try:
            token = auth_header.replace("Bearer ", "")
            payload = decode_token(token)
            
            if not payload or not isinstance(payload, dict):
                return Response(
                    content='{"detail": "Token invalide"}',
                    status_code=401,
                    media_type="application/json"
                )
            
            token_type = payload.get("type")
            if token_type != "access":
                return Response(
                    content='{"detail": "Token invalide - type incorrect"}',
                    status_code=401,
                    media_type="application/json"
                )
            
            request.state.user = payload
            
        except Exception as e:
            logger.error(f"Erreur validation token: {e}")
            return Response(
                content='{"detail": "Token invalide ou expiré"}',
                status_code=401,
                media_type="application/json"
            )
        
        response = await call_next(request)
        return response


# Alias pour la compatibilité avec d'autres fichiers qui pourraient importer ce nom
UnifiedAuthSubscriptionMiddleware = AuthMiddleware