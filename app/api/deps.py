# app/api/deps.py

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.core.config import settings
from app.services.subscription_service import is_subscription_active
from app.core.security import verify_token as security_verify_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
logger = logging.getLogger(__name__)


# ======================================================
# AUTHENTIFICATION UTILISATEUR
# ======================================================

def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """Récupère l'utilisateur courant à partir du token"""
    try:
        payload = security_verify_token(token)
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide"
            )
        
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé"
            )
        
        # Vérifier si le compte est actif
        if not user.actif:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte désactivé"
            )
        
        return user
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur vérification token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide"
        )

def get_super_admin_user(current_user: User = Depends(get_current_user)):
    """Vérifie que l'utilisateur est un super administrateur"""
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Droits super administrateur requis"
        )
    return current_user


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Vérifie que l'utilisateur est actif"""

    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Utilisateur inactif ou suspendu"
        )

    return current_user


# ======================================================
# TENANT
# ======================================================

def get_current_tenant(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Tenant:
    """
    Récupère le tenant associé à l'utilisateur courant
    """
    # Si l'utilisateur a un tenant_id, l'utiliser
    if not current_user.tenant_id:
        # Pour les comptes admin ou système, on pourrait avoir une logique différente
        raise HTTPException(
            status_code=400,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    if not tenant:
        raise HTTPException(
            status_code=404,
            detail="Tenant introuvable"
        )
    
    if tenant.status not in ("active", "trial"):
        raise HTTPException(
            status_code=403,
            detail=f"Tenant {tenant.status} – accès refusé"
        )
    
    return tenant


def get_tenant_id_from_request(request: Request) -> Optional[str]:
    """
    Récupère le tenant ID de la requête de manière flexible
    """
    # 1. Essayer depuis les headers
    tenant_id = request.headers.get("X-Tenant-ID")
    
    # 2. Essayer depuis le token JWT (pour les routes authentifiées)
    if not tenant_id:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                payload = jwt.decode(
                    token, 
                    settings.SECRET_KEY, 
                    algorithms=[settings.ALGORITHM],
                    options={"verify_signature": False}  # Juste pour lire le payload
                )
                tenant_id = payload.get("tenant_id")
            except Exception:
                pass
    
    # 3. Essayer depuis l'état de la requête (si middleware a déjà ajouté)
    if not tenant_id and hasattr(request.state, "tenant_id"):
        tenant_id = request.state.tenant_id
    
    return tenant_id


def get_current_tenant_with_fallback(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Tenant:
    """
    Version plus flexible qui peut récupérer le tenant de plusieurs sources
    """
    # Si l'utilisateur a déjà un tenant_id, l'utiliser
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            return tenant
    
    # Sinon, essayer de récupérer depuis la requête
    if request:
        tenant_id = get_tenant_id_from_request(request)
        if tenant_id:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                return tenant
    
    # Si on arrive ici, lever une exception
    raise HTTPException(
        status_code=400,
        detail="Tenant non spécifié et non trouvé dans le profil utilisateur"
    )


# ======================================================
# ABONNEMENT
# ======================================================

def subscription_required(
    tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> Tenant:
    """Vérifie que l'abonnement du tenant est actif"""

    if not is_subscription_active(db, tenant.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Abonnement expiré ou inactif"
        )

    return tenant


# ======================================================
# PHARMACIE
# ======================================================

def get_current_pharmacy(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """
    Récupère la pharmacie courante basée sur plusieurs sources.
    Priorité : Header > Token JWT > Utilisateur principal > Première pharmacie active
    """
    
    pharmacy_id = None
    pharmacy_data = None
    
    # 1. Chercher dans les headers de la requête
    pharmacy_id = request.headers.get("X-Pharmacy-ID")
    
    # 2. Chercher dans le token JWT
    if not pharmacy_id:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                payload = jwt.decode(
                    token,
                    settings.SECRET_KEY,
                    algorithms=[settings.ALGORITHM],
                    options={"verify_signature": False}
                )
                pharmacy_id = payload.get("pharmacy_id")
            except Exception:
                pass
    
    # 3. Chercher dans le profil utilisateur (pharmacy_id ou relations)
    if not pharmacy_id:
        # Si l'utilisateur a un pharmacy_id direct
        if current_user.pharmacy_id:
            pharmacy_id = str(current_user.pharmacy_id)
        else:
            # Chercher dans les relations utilisateur-pharmacie
            # Import ici pour éviter les imports circulaires
            from app.models.user_pharmacy import UserPharmacy
            
            user_pharmacy = db.query(UserPharmacy).filter(
                UserPharmacy.user_id == current_user.id,
                UserPharmacy.is_active == True
            ).order_by(UserPharmacy.is_primary.desc()).first()
            
            if user_pharmacy:
                pharmacy_id = str(user_pharmacy.pharmacy_id)
    
    # 4. Si on a un ID, récupérer la pharmacie
    if pharmacy_id:
        try:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == UUID(pharmacy_id),
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active == True
            ).first()
            
            if pharmacy:
                pharmacy_data = {
                    "id": pharmacy.id,
                    "name": pharmacy.name,
                    "license_number": pharmacy.license_number,
                    "pharmacy_code": pharmacy.pharmacy_code,
                    "address": pharmacy.address,
                    "city": pharmacy.city,
                    "country": pharmacy.country,
                    "phone": pharmacy.phone,
                    "email": pharmacy.email,
                    "is_main": pharmacy.is_main,
                    "config": pharmacy.config or {}
                }
        except (ValueError, Exception) as e:
            logger.warning(f"Erreur lors de la récupération de la pharmacie {pharmacy_id}: {str(e)}")
    
    # 5. Si pas de pharmacie spécifique mais l'utilisateur a le droit d'accéder à toutes
    if not pharmacy_data and current_user.role in ["admin", "superviseur", "gerant"]:
        # Récupérer la pharmacie principale ou la première active
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).order_by(Pharmacy.is_main.desc()).first()
        
        if pharmacy:
            pharmacy_data = {
                "id": pharmacy.id,
                "name": pharmacy.name,
                "license_number": pharmacy.license_number,
                "pharmacy_code": pharmacy.pharmacy_code,
                "address": pharmacy.address,
                "city": pharmacy.city,
                "country": pharmacy.country,
                "phone": pharmacy.phone,
                "email": pharmacy.email,
                "is_main": pharmacy.is_main,
                "config": pharmacy.config or {}
            }
    
    # 6. Pour les autres rôles sans pharmacie, retourner None
    return pharmacy_data


def require_pharmacy_access(
    allow_admin_override: bool = True
):
    """
    Décorateur pour s'assurer que l'utilisateur a accès à une pharmacie.
    
    Args:
        allow_admin_override: Si True, les admins peuvent accéder sans pharmacie spécifique
    """
    def pharmacy_checker(
        current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
        current_user: User = Depends(get_current_active_user)
    ) -> Dict[str, Any]:
        
        if not current_pharmacy:
            if allow_admin_override and current_user.role in ["admin", "superviseur"]:
                # Les admins/superviseurs peuvent ne pas avoir de pharmacie spécifique
                # Ils ont un accès global
                return {"id": None, "name": "Global Access", "is_global": True}
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Accès pharmacie requis pour cette opération"
                )
        
        return current_pharmacy
    
    return pharmacy_checker


def get_pharmacy_or_main(
    pharmacy_id: Optional[UUID] = None,
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Pharmacy:
    """
    Récupère une pharmacie spécifique ou la pharmacie principale du tenant.
    Vérifie les permissions d'accès.
    """
    
    if pharmacy_id:
        # Récupérer la pharmacie spécifique
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).first()
        
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pharmacie non trouvée"
            )
        
        # Vérifier l'accès utilisateur
        if not can_user_access_pharmacy(current_user, pharmacy, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette pharmacie"
            )
        
        return pharmacy
    
    # Sinon, récupérer la pharmacie principale
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active == True,
        Pharmacy.is_main == True
    ).first()
    
    if not pharmacy:
        # Fallback sur la première pharmacie active
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune pharmacie active trouvée pour ce tenant"
        )
    
    return pharmacy


def can_user_access_pharmacy(
    user: User,
    pharmacy: Pharmacy,
    db: Session
) -> bool:
    """
    Vérifie si un utilisateur a accès à une pharmacie.
    """
    # Les admins et superviseurs ont accès à toutes les pharmacies
    if user.role in ["admin", "superviseur"]:
        return True
    
    # Vérifier si l'utilisateur a un pharmacy_id direct
    if user.pharmacy_id == pharmacy.id:
        return True
    
    # Vérifier dans la table d'association
    from app.models.user_pharmacy import UserPharmacy
    
    association = db.query(UserPharmacy).filter(
        UserPharmacy.user_id == user.id,
        UserPharmacy.pharmacy_id == pharmacy.id,
        UserPharmacy.is_active == True
    ).first()
    
    return association is not None


# ======================================================
# ROLES & PERMISSIONS
# ======================================================

def require_role(required_roles: List[str]):
    """Vérifie le rôle de l'utilisateur"""

    def role_checker(
        current_user: User = Depends(get_current_active_user)
    ) -> User:

        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rôle requis : {', '.join(required_roles)}"
            )

        return current_user

    return role_checker


def require_permission(permission: str):
    """Vérifie les permissions de l'utilisateur"""

    def permission_checker(
        current_user: User = Depends(get_current_active_user)
    ) -> User:

        # Permissions basées sur les rôles
        permission_map = {
            "admin": [
                # Stock
                "view_stock", "manage_stock", "export_stock", "adjust_stock",
                # Ventes
                "ventes:create", "ventes:read", "ventes:update", "ventes:delete",
                "ventes:stats", "ventes:export", "ventes:cancel",
                # Pharmacies
                "manage_pharmacies", "view_pharmacies",
                # Utilisateurs
                "manage_users", "view_users",
                # Rapports
                "view_reports", "export_reports",
                # Configuration
                "manage_settings"
            ],
            "gerant": [
                # Stock
                "view_stock", "manage_stock", "export_stock", "adjust_stock",
                # Ventes
                "ventes:create", "ventes:read", "ventes:update",
                "ventes:stats", "ventes:export", "ventes:cancel",
                # Pharmacies
                "view_pharmacies",
                # Utilisateurs (limité à sa pharmacie)
                "view_users",
                # Rapports
                "view_reports", "export_reports"
            ],
            "pharmacien": [
                # Stock
                "view_stock", "manage_stock", "adjust_stock",
                # Ventes
                "ventes:create", "ventes:read", "ventes:cancel",
                # Rapports limités
                "view_reports"
            ],
            "vendeur": ["ventes:create", "ventes:read", "view_stock"],
            "caissier": ["ventes:create", "ventes:read", "ventes:cancel"],
            "superviseur": [
                "ventes:read", "ventes:stats", "ventes:export",
                "view_stock", "view_reports", "export_reports",
                "view_pharmacies", "view_users"
            ],
            "technicien": ["view_stock", "adjust_stock"]
        }

        user_permissions = permission_map.get(current_user.role, [])

        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission requise : {permission}"
            )

        return current_user

    return permission_checker


# ======================================================
# UTILISATEUR OPTIONNEL (PUBLIC / STATS)
# ======================================================

def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Retourne l'utilisateur courant si token présent"""

    if not token:
        return None

    try:
        return get_current_user(token, db)
    except HTTPException:
        return None


def get_current_user_with_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy)
) -> Dict[str, Any]:
    """
    Retourne un contexte complet avec utilisateur, tenant et pharmacie
    """
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "has_pharmacy_access": current_pharmacy is not None
    }


# ======================================================
# DEPENDANCES COMBINEES
# ======================================================

def get_authenticated_context(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Dépendance combinée qui retourne tout le contexte d'authentification
    """
    current_user = get_current_active_user(request=request, db=db)
    current_tenant = get_current_tenant(current_user=current_user, db=db)
    current_pharmacy = get_current_pharmacy(
        request=request,
        current_user=current_user,
        current_tenant=current_tenant,
        db=db
    )
    
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "db": db
    }


# ======================================================
# EXPORTS
# ======================================================

__all__ = [
    # Authentification de base
    "get_db",
    "get_current_user",
    "get_current_active_user",
    "get_optional_current_user",
    
    # Tenant
    "get_current_tenant",
    "get_current_tenant_with_fallback",
    "get_tenant_id_from_request",
    
    # Pharmacie
    "get_current_pharmacy",
    "require_pharmacy_access",
    "get_pharmacy_or_main",
    "can_user_access_pharmacy",
    
    # Abonnement
    "subscription_required",
    
    # Rôles et permissions
    "require_role",
    "require_permission",
    
    # Contexte combiné
    "get_current_user_with_context",
    "get_authenticated_context",
    
    # Autres
    "oauth2_scheme"
]