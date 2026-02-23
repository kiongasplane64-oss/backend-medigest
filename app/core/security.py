# app/core/security.py (version corrigée)
from datetime import datetime, timedelta
from typing import Optional, List, Callable, Any, Dict
from functools import wraps
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
import re
import secrets
import string

from app.models.user import User
from app.db.session import get_db
from app.core.config import settings

# ===========================================
# GESTION DES MOTS DE PASSE
# ===========================================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_password_hash(password: str) -> str:
    """Hash d'un mot de passe"""
    return pwd_context.hash(password)

def hash_password(password: str) -> str:
    """Hash d'un mot de passe (alias pour get_password_hash)"""
    return get_password_hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Vérifie qu'un mot de passe correspond à son hash"""
    return pwd_context.verify(plain_password, hashed_password)

# ===========================================
# JWT TOKENS - FONCTIONS MANQUANTES
# ===========================================
def verify_token(token: str) -> Dict[str, Any]:
    """Vérifie et décode un token JWT"""
    try:
        payload = jwt.decode(
            token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token JWT invalide: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Erreur de validation du token: {str(e)}"
        )

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un token JWT d'accès"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un token JWT de rafraîchissement"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def decode_token(token: str) -> Dict[str, Any]:
    """Décode un token JWT"""
    return verify_token(token)  # Utilise la même fonction

def decode_access_token(token: str) -> Dict[str, Any]:
    """Décode un token d'accès JWT (alias pour decode_token avec vérification du type)"""
    try:
        payload = decode_token(token)
        token_type = payload.get("type")
        
        if token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide: ce n'est pas un token d'accès",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Erreur de décodage du token d'accès: {str(e)}"
        )

# ===========================================
# OAUTH2 & AUTHENTIFICATION
# ===========================================
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """Récupère l'utilisateur courant depuis le token"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Impossible de valider les informations d'identification",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = verify_token(token)
        user_id: str = payload.get("sub")
        tenant_id: str = payload.get("tenant_id")
        token_type: str = payload.get("type")
        
        if user_id is None or tenant_id is None:
            raise credentials_exception
            
        if token_type != "access":
            raise credentials_exception
            
    except HTTPException:
        raise
    except Exception:
        raise credentials_exception

    # Récupérer l'utilisateur depuis la base de données
    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == tenant_id,
        User.actif == True
    ).first()

    if user is None:
        raise credentials_exception
    
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Vérifie que l'utilisateur est actif"""
    if not current_user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte utilisateur désactivé"
        )
    return current_user

async def get_current_superuser(current_user: User = Depends(get_current_user)) -> User:
    """Vérifie que l'utilisateur est super admin"""
    if current_user.role not in ["super_admin", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Privilèges insuffisants"
        )
    return current_user

# ===========================================
# PERMISSIONS ET RÔLES
# ===========================================
ROLE_PERMISSIONS = {
    "super_admin": ["*"],  # Toutes les permissions
    "admin": ["*"],
    "pharmacien": ["ventes:create", "ventes:read", "ventes:update", "inventory:read", 
                   "inventory:update", "clients:read", "clients:create", "reports:read"],
    "gerant": ["ventes:create", "ventes:read", "ventes:update", "ventes:delete", 
               "inventory:*", "clients:*", "reports:*"],
    "vendeur": ["ventes:create", "ventes:read", "clients:read"],
    "caissier": ["ventes:create", "ventes:read"],
}

def has_permission(user_role: str, permission: str) -> bool:
    """Vérifie si un rôle a une permission"""
    permissions = ROLE_PERMISSIONS.get(user_role, [])
    
    # Si l'utilisateur a "*", il a toutes les permissions
    if "*" in permissions:
        return True
    
    # Vérifier la permission exacte
    if permission in permissions:
        return True
    
    # Vérifier les permissions de module (ex: "ventes:*")
    if ":" in permission:
        module = permission.split(":")[0] + ":*"
        return module in permissions
    
    return False

def require_permission(permission_code: str):
    """Décorateur pour vérifier les permissions"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Trouver l'utilisateur dans les arguments
            current_user = None
            for arg in kwargs.values():
                if isinstance(arg, User):
                    current_user = arg
                    break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier la permission
            if not has_permission(current_user.role, permission_code):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Permission '{permission_code}' requise. Rôle: {current_user.role}"
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator

def require_role(allowed_roles: List[str]):
    """Décorateur pour vérifier le rôle"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Trouver l'utilisateur dans les arguments
            current_user = None
            for arg in kwargs.values():
                if isinstance(arg, User):
                    current_user = arg
                    break
            
            if not current_user:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Utilisateur non authentifié"
                )
            
            # Vérifier le rôle
            if current_user.role not in allowed_roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Rôles autorisés: {allowed_roles}. Votre rôle: {current_user.role}"
                )
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator

# ===========================================
# VALIDATION
# ===========================================
def validate_password_strength(password: str) -> Dict[str, bool]:
    """Valide la force d'un mot de passe"""
    validation = {
        "length": len(password) >= 8,
        "uppercase": any(c.isupper() for c in password),
        "lowercase": any(c.islower() for c in password),
        "digit": any(c.isdigit() for c in password),
        "special": any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?`~' for c in password),
    }
    return validation

def is_password_strong(password: str) -> bool:
    """Vérifie si un mot de passe est fort"""
    validation = validate_password_strength(password)
    return all(validation.values())

def validate_email(email: str) -> bool:
    """Valide un email"""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_phone(phone: str) -> bool:
    """Valide un numéro de téléphone"""
    cleaned = re.sub(r'[^\d+]', '', phone)
    pattern = r'^(\+?243|0)[0-9]{9}$'
    return bool(re.match(pattern, cleaned))

# ===========================================
# GÉNÉRATION
# ===========================================
def generate_api_key(length: int = 32) -> str:
    """Génère une clé API sécurisée"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_verification_token(length: int = 32) -> str:
    """Génère un token de vérification"""
    return secrets.token_urlsafe(length)

def generate_password_reset_token(length: int = 32) -> str:
    """Génère un token de réinitialisation de mot de passe"""
    return secrets.token_urlsafe(length)

def generate_invitation_code(length: int = 8) -> str:
    """Génère un code d'invitation"""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

# ===========================================
# FONCTIONS D'AUTHENTIFICATION
# ===========================================
def authenticate_user(db: Session, email: str, password: str, tenant_id: str = None) -> Optional[User]:
    """Authentifie un utilisateur"""
    query = db.query(User).filter(User.email == email, User.actif == True)
    
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    
    user = query.first()
    
    if not user:
        return None
    
    if not verify_password(password, user.hashed_password):
        return None
    
    return user

async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm,
    db: Session = Depends(get_db),
    tenant_id: str = None
) -> Dict[str, Any]:
    """Connexion et génération de token"""
    user = authenticate_user(db, form_data.username, form_data.password, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Créer les tokens
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    
    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "email": user.email
        },
        expires_delta=access_token_expires
    )
    
    refresh_token = create_refresh_token(
        data={
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role
        },
        expires_delta=refresh_token_expires
    )
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id)
        }
    }

# ===========================================
# UTILITAIRES
# ===========================================
def check_password_reuse(new_password: str, old_hashed_password: str) -> bool:
    """Vérifie si le nouveau mot de passe a déjà été utilisé"""
    return verify_password(new_password, old_hashed_password)

def sanitize_input(input_string: str) -> str:
    """Nettoie une chaîne de caractères pour prévenir les injections"""
    if not input_string:
        return ""
    
    dangerous_chars = ["<", ">", "'", "\"", ";", "--", "/*", "*/"]
    for char in dangerous_chars:
        input_string = input_string.replace(char, "")
    
    return input_string.strip()