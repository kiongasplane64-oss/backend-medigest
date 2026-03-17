# app/api/deps.py
"""
Dépendances FastAPI pour l'authentification, les autorisations et le contexte
multi-tenant de l'application SaaS Medigest.
"""

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any, Union
from uuid import UUID
import logging
from datetime import datetime, timedelta

from app.db.session import get_db
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.core.config import settings
from app.core.security import verify_token as security_verify_token
from app.services.subscription_service import (
    check_user_subscription,
    check_tenant_limits,
    can_user_access_feature
)

# Configuration OAuth2
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False
)

logger = logging.getLogger(__name__)


# =============================================================================
# 1. AUTHENTIFICATION UTILISATEUR
# =============================================================================

def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme)
) -> User:
    """
    Récupère l'utilisateur courant à partir du token JWT.
    Version améliorée avec meilleure gestion des erreurs et logs détaillés.
    """
    logger.info(f"🔐 get_current_user - Token présent: {bool(token)}")
    
    if not token:
        logger.warning("❌ Token d'authentification manquant")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token d'authentification manquant",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    try:
        # Log partiel du token pour débogage (sécurisé)
        token_preview = f"{token[:15]}...{token[-10:]}" if len(token) > 25 else token
        logger.debug(f"🔑 Validation du token: {token_preview}")
        
        payload = security_verify_token(token)
        logger.info(f"📦 Payload décodé: {payload}")
        
        user_id = payload.get("sub")
        
        if not user_id:
            logger.warning("❌ Token invalide : aucun identifiant utilisateur")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide : aucun identifiant utilisateur",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        # Vérifier si c'est un token d'impersonation
        is_impersonation = payload.get("is_impersonation", False)
        impersonated_by = payload.get("impersonated_by")
        
        # Récupérer l'utilisateur
        user = db.query(User).filter(User.id == user_id).first()
        
        if not user:
            logger.warning(f"❌ Utilisateur non trouvé pour l'ID: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé"
            )
        
        logger.info(f"✅ Utilisateur trouvé: {user.email}, rôle: {user.role}, actif: {user.actif}")
        
        # Vérifier si le compte est actif
        if not user.actif:
            logger.warning(f"⚠️ Compte désactivé: {user.email}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte désactivé"
            )
        
        # Stocker les métadonnées d'impersonation
        user.is_impersonated = is_impersonation
        user.impersonated_by = impersonated_by
        
        return user
        
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        logger.warning("❌ Token expiré")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expiré",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.JWTError as e:
        logger.warning(f"❌ Erreur JWT: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except Exception as e:
        logger.error(f"❌ Erreur inattendue: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Erreur d'authentification",
            headers={"WWW-Authenticate": "Bearer"}
        )


def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Vérifie que l'utilisateur courant est actif et non verrouillé.
    """
    logger.info(f"🔐 Vérification utilisateur actif: {current_user.email}")
    
    # Vérifier si le compte est verrouillé
    if current_user.locked_until and current_user.locked_until > datetime.utcnow():
        remaining = int((current_user.locked_until - datetime.utcnow()).total_seconds() / 60)
        logger.warning(f"⚠️ Compte verrouillé: {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Compte verrouillé. Réessayez dans {remaining} minutes."
        )
    
    # Vérifier si le compte est actif
    if not current_user.actif:
        logger.warning(f"⚠️ Compte désactivé: {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé"
        )
    
    return current_user


def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Retourne l'utilisateur courant si un token valide est fourni,
    sinon retourne None (pour les routes publiques).
    """
    if not token:
        return None
    
    try:
        return get_current_user(db=db, token=token)
    except HTTPException:
        return None


def get_super_admin_user(
    current_user: User = Depends(get_current_active_user)
) -> User:
    """
    Vérifie que l'utilisateur est un super administrateur.
    Version améliorée avec plus de flexibilité.
    """
    logger.info(f"🔐 Vérification super admin - Rôle: '{current_user.role}'")
    
    # Accepter plusieurs variantes du rôle super admin
    allowed_roles = ["super_admin", "superadmin", "admin"]
    
    if current_user.role not in allowed_roles:
        logger.warning(f"⛔ Accès super admin refusé pour {current_user.email} (rôle: {current_user.role})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "insufficient_role",
                "message": "Accès réservé aux super administrateurs",
                "current_role": current_user.role,
                "required_role": "super_admin"
            }
        )
    
    logger.info(f"✅ Accès super admin autorisé pour {current_user.email}")
    return current_user


# =============================================================================
# 2. CONTEXTE TENANT (MULTI-TENANT)
# =============================================================================

def get_current_tenant(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Optional[Tenant]:
    """
    Récupère le tenant associé à l'utilisateur courant.
    Retourne None pour les super admins (pas d'erreur).
    """
    # Les super admins n'ont pas de tenant
    if current_user.role in ["super_admin", "superadmin"]:
        logger.info(f"ℹ️ Super admin sans tenant: {current_user.email}")
        return None
    
    if not current_user.tenant_id:
        logger.warning(f"⚠️ Utilisateur sans tenant: {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utilisateur non associé à un tenant"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    if not tenant:
        logger.error(f"❌ Tenant introuvable pour l'utilisateur {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant introuvable"
        )
    
    # Vérifier le statut du tenant
    if tenant.status not in ("active", "trial"):
        status_messages = {
            "suspended": "Votre compte a été suspendu. Contactez le support.",
            "expired": "Votre abonnement a expiré.",
            "cancelled": "Votre compte a été annulé.",
            "draft": "Votre compte n'est pas encore activé."
        }
        
        message = status_messages.get(tenant.status, f"Tenant {tenant.status}")
        logger.warning(f"⚠️ Tenant {tenant.tenant_code} - {message}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=message
        )
    
    return tenant


def get_tenant_id_from_request(request: Request) -> Optional[str]:
    """
    Récupère l'ID du tenant depuis la requête de manière flexible.
    """
    # 1. Header explicite
    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return tenant_id
    
    # 2. Subdomain (ex: tenant1.example.com)
    host = request.headers.get("host", "")
    if host and '.' in host:
        subdomain = host.split('.')[0]
        if subdomain and subdomain not in ["www", "app", "api", "localhost"]:
            return subdomain
    
    # 3. Depuis l'état de la requête (si middleware a déjà ajouté)
    if hasattr(request.state, "tenant_id"):
        return request.state.tenant_id
    
    return None


def get_current_tenant_with_fallback(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Optional[Tenant]:
    """
    Version plus flexible qui peut récupérer le tenant depuis l'utilisateur
    ou depuis la requête (headers, subdomain).
    Retourne None pour les super admins.
    """
    # 1. Depuis l'utilisateur
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            return tenant
    
    # 2. Super admin sans tenant
    if current_user.role in ["super_admin", "superadmin"]:
        logger.info(f"ℹ️ Super admin sans tenant: {current_user.email}")
        return None
    
    # 3. Depuis la requête
    tenant_id = get_tenant_id_from_request(request)
    if tenant_id:
        try:
            # Essayer de parser comme UUID
            tenant_uuid = UUID(tenant_id)
            tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
        except (ValueError, TypeError):
            # Chercher par tenant_code ou slug
            tenant = db.query(Tenant).filter(
                (Tenant.tenant_code == tenant_id) | (Tenant.slug == tenant_id)
            ).first()
        
        if tenant:
            return tenant
    
    # 4. Aucun tenant trouvé
    logger.warning(f"⚠️ Aucun tenant trouvé pour l'utilisateur {current_user.email}")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Tenant non spécifié et non trouvé dans le profil utilisateur"
    )


# =============================================================================
# 3. ABONNEMENT ET LIMITES
# =============================================================================

async def require_active_subscription(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> User:
    """
    Vérifie que l'utilisateur a un abonnement actif.
    Pour les routes qui nécessitent un accès complet (FULL).
    """
    # Les super admins ont toujours accès
    if current_user.role in ["super_admin", "superadmin"]:
        logger.info(f"✅ Super admin - accès illimité: {current_user.email}")
        return current_user
    
    sub_status = check_user_subscription(db, str(current_user.id))
    
    if not sub_status["has_subscription"]:
        logger.warning(f"⚠️ Pas d'abonnement pour {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_subscription",
                "message": "Vous n'avez pas d'abonnement actif",
                "mode": "READ_ONLY",
                "action": "subscribe"
            }
        )
    
    if sub_status["mode"] == "READ_ONLY":
        error_detail = {
            "error": "subscription_expired",
            "message": sub_status.get("message", "Votre abonnement a expiré"),
            "mode": "READ_ONLY"
        }
        
        if sub_status.get("expired_date"):
            error_detail["expired_date"] = sub_status["expired_date"]
        
        logger.warning(f"⚠️ Abonnement expiré pour {current_user.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail
        )
    
    # Ajouter les infos d'abonnement à l'objet user pour usage ultérieur
    current_user.subscription_info = sub_status
    
    return current_user


async def check_admin_limits(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    action: Optional[str] = None
) -> User:
    """
    Vérifie que l'admin peut effectuer une action selon les limites de son plan.
    """
    # Seuls les admins sont concernés par les limites
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action réservée aux administrateurs"
        )
    
    # Vérifier d'abord l'abonnement
    sub_status = check_user_subscription(db, str(current_user.id))
    
    if not sub_status["has_subscription"] or sub_status["mode"] == "READ_ONLY":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_required",
                "message": "Un abonnement actif est requis pour cette action",
                "mode": "READ_ONLY"
            }
        )
    
    # Vérifier les limites spécifiques
    limits = check_tenant_limits(db, str(current_user.tenant_id))
    
    # Stocker les limites dans l'objet user
    current_user.tenant_limits = limits
    
    if action == "create_user" and not limits["can_create_user"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "user_limit_reached",
                "message": f"Limite d'utilisateurs atteinte ({limits['current_usage']['users']} / {limits['limits']['max_users']})",
                "current": limits["current_usage"]["users"],
                "limit": limits["limits"]["max_users"],
                "suggestion": "Passez à un plan supérieur pour ajouter plus d'utilisateurs"
            }
        )
    
    if action == "create_pharmacy" and not limits["can_create_pharmacy"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "pharmacy_limit_reached",
                "message": f"Limite de pharmacies atteinte ({limits['current_usage']['pharmacies']} / {limits['limits']['max_pharmacies']})",
                "current": limits["current_usage"]["pharmacies"],
                "limit": limits["limits"]["max_pharmacies"],
                "suggestion": "Passez à un plan supérieur pour créer plus de pharmacies"
            }
        )
    
    # Vérification générique pour d'autres actions
    if action and not can_user_access_feature(current_user, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "feature_not_available",
                "message": f"Cette fonctionnalité n'est pas disponible dans votre plan actuel",
                "feature": action,
                "plan": limits["plan"]
            }
        )
    
    return current_user


# =============================================================================
# 4. CONTEXTE PHARMACIE
# =============================================================================

def get_current_pharmacy(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> Optional[Dict[str, Any]]:
    """
    Récupère la pharmacie courante basée sur plusieurs sources.
    Adapté pour les super admins.
    """
    # Les super admins n'ont pas de pharmacie par défaut
    if current_user.role in ["super_admin", "superadmin"]:
        logger.info(f"ℹ️ Super admin - pas de pharmacie associée")
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role
        }
    
    pharmacy_id = None
    pharmacy_data = None
    
    # 1. Header explicite
    pharmacy_id = request.headers.get("X-Pharmacy-ID")
    
    # 2. Token JWT (payload)
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
    
    # 3. Si on a un ID, récupérer la pharmacie
    if pharmacy_id and current_tenant:
        try:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == UUID(pharmacy_id),
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active == True
            ).first()
            
            if pharmacy:
                pharmacy_data = {
                    "id": str(pharmacy.id),
                    "name": pharmacy.name,
                    "license_number": pharmacy.license_number,
                    "pharmacy_code": pharmacy.pharmacy_code,
                    "address": pharmacy.address,
                    "city": pharmacy.city,
                    "country": pharmacy.country,
                    "phone": pharmacy.phone,
                    "email": pharmacy.email,
                    "is_main": pharmacy.is_main,
                    "is_active": pharmacy.is_active,
                    "config": pharmacy.config or {}
                }
        except (ValueError, Exception) as e:
            logger.warning(f"⚠️ Erreur récupération pharmacie {pharmacy_id}: {e}")
    
    return pharmacy_data


def require_pharmacy_access(
    allow_admin_override: bool = True,
    require_active: bool = True
):
    """
    Dépendance factory pour s'assurer que l'utilisateur a accès à une pharmacie.
    """
    def pharmacy_checker(
        current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
        current_user: User = Depends(get_current_active_user)
    ) -> Dict[str, Any]:
        
        if not current_pharmacy:
            if allow_admin_override and current_user.role in ["admin", "superviseur", "super_admin", "superadmin"]:
                return {
                    "id": None,
                    "name": "Accès global",
                    "is_global": True,
                    "role": current_user.role
                }
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "pharmacy_required",
                        "message": "Une pharmacie doit être sélectionnée pour cette opération"
                    }
                )
        
        return current_pharmacy
    
    return pharmacy_checker


def get_pharmacy_or_main(
    pharmacy_id: Optional[UUID] = None,
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
) -> Optional[Pharmacy]:
    """
    Récupère une pharmacie spécifique ou la pharmacie principale du tenant.
    Adapté pour les super admins.
    """
    # Super admin - retourne None ou la pharmacie demandée si elle existe
    if current_user.role in ["super_admin", "superadmin"]:
        if pharmacy_id:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.is_active == True
            ).first()
            
            if pharmacy:
                return pharmacy
        return None
    
    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant non trouvé"
        )
    
    if pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).first()
        
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "pharmacy_not_found",
                    "message": "Pharmacie non trouvée ou inactive"
                }
            )
        
        if not can_user_access_pharmacy(current_user, pharmacy, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "access_denied",
                    "message": "Vous n'avez pas accès à cette pharmacie"
                }
            )
        
        return pharmacy
    
    # Pharmacie principale
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active == True,
        Pharmacy.is_main == True
    ).first()
    
    if not pharmacy:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active == True
        ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "no_pharmacy",
                "message": "Aucune pharmacie active trouvée pour ce tenant"
            }
        )
    
    return pharmacy


def can_user_access_pharmacy(
    user: User,
    pharmacy: Pharmacy,
    db: Session
) -> bool:
    """
    Vérifie si un utilisateur a accès à une pharmacie spécifique.
    """
    # Super admin a accès partout
    if user.role in ["super_admin", "superadmin"]:
        return True
    
    # Admin du tenant a accès à toutes les pharmacies du tenant
    if user.role == "admin" and user.tenant_id == pharmacy.tenant_id:
        return True
    
    # Vérifier dans la table d'association
    from app.models.user_pharmacy import UserPharmacy
    
    association = db.query(UserPharmacy).filter(
        UserPharmacy.user_id == user.id,
        UserPharmacy.pharmacy_id == pharmacy.id
    ).first()
    
    return association is not None


# =============================================================================
# 5. RÔLES ET PERMISSIONS
# =============================================================================

def require_role(required_roles: List[str]):
    """
    Dépendance factory pour vérifier le rôle de l'utilisateur.
    """
    def role_checker(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if current_user.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_role",
                    "message": f"Rôle requis : {' ou '.join(required_roles)}",
                    "current_role": current_user.role,
                    "required_roles": required_roles
                }
            )
        return current_user
    return role_checker


def require_permission(permission: str):
    """
    Dépendance factory pour vérifier une permission spécifique.
    """
    PERMISSION_MAP = {
        "super_admin": ["*"],
        "superadmin": ["*"],
        
        "admin": [
            "stock:view", "stock:create", "stock:update", "stock:delete", "stock:export",
            "stock:adjust", "stock:transfer",
            "sales:view", "sales:create", "sales:update", "sales:delete", "sales:cancel",
            "sales:stats", "sales:export",
            "pharmacy:view", "pharmacy:create", "pharmacy:update", "pharmacy:delete",
            "user:view", "user:create", "user:update", "user:delete",
            "report:view", "report:export",
            "settings:view", "settings:update"
        ],
        
        "gerant": [
            "stock:view", "stock:create", "stock:update", "stock:adjust",
            "sales:view", "sales:create", "sales:cancel", "sales:stats",
            "pharmacy:view",
            "user:view",
            "report:view", "report:export"
        ],
        
        "pharmacien": [
            "stock:view", "stock:create", "stock:update", "stock:adjust",
            "sales:view", "sales:create", "sales:cancel",
            "report:view"
        ],
        
        "vendeur": [
            "stock:view",
            "sales:create", "sales:view"
        ],
        
        "caissier": [
            "sales:create", "sales:view", "sales:cancel"
        ],
        
        "superviseur": [
            "stock:view",
            "sales:view", "sales:stats", "sales:export",
            "pharmacy:view",
            "user:view",
            "report:view", "report:export"
        ],
        
        "technicien": [
            "stock:view", "stock:adjust"
        ]
    }
    
    def permission_checker(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if current_user.role in ["super_admin", "superadmin"]:
            return current_user
        
        user_permissions = PERMISSION_MAP.get(current_user.role, [])
        
        if permission not in user_permissions and "*" not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "message": f"Permission requise : {permission}",
                    "current_role": current_user.role,
                    "required_permission": permission
                }
            )
        
        return current_user
    
    return permission_checker


# =============================================================================
# 6. CONTEXTE COMBINÉ
# =============================================================================

def get_authenticated_context(
    request: Request,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """
    Dépendance combinée qui retourne tout le contexte d'authentification.
    """
    current_user = get_current_active_user(request=request, db=db)
    current_tenant = get_current_tenant(current_user=current_user, db=db)
    current_pharmacy = get_current_pharmacy(
        request=request,
        current_user=current_user,
        current_tenant=current_tenant,
        db=db
    )
    
    # Ajouter les infos d'abonnement
    sub_status = None
    if current_user.role not in ["super_admin", "superadmin"]:
        sub_status = check_user_subscription(db, str(current_user.id))
    
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "subscription": sub_status,
        "db": db
    }


def get_current_user_with_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy)
) -> Dict[str, Any]:
    """
    Retourne un contexte utilisateur enrichi.
    """
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "has_pharmacy_access": current_pharmacy is not None,
        "role": current_user.role,
        "is_super_admin": current_user.role in ["super_admin", "superadmin"]
    }


# =============================================================================
# 7. UTILITAIRES
# =============================================================================

def get_pagination_params(
    page: int = 1,
    limit: int = 20
) -> Dict[str, int]:
    """
    Valide et retourne les paramètres de pagination.
    """
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le numéro de page doit être >= 1"
        )
    
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La limite doit être comprise entre 1 et 100"
        )
    
    return {
        "page": page,
        "limit": limit,
        "skip": (page - 1) * limit
    }


def get_date_range_params(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Optional[datetime]]:
    """
    Valide et retourne les paramètres de plage de dates.
    """
    result = {"start_date": None, "end_date": None}
    
    if start_date:
        try:
            result["start_date"] = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de début invalide. Utilisez ISO format (YYYY-MM-DD)"
            )
    
    if end_date:
        try:
            result["end_date"] = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de fin invalide. Utilisez ISO format (YYYY-MM-DD)"
            )
    
    return result


def subscription_required(
    tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db)
) -> Optional[Tenant]:
    """
    Vérifie que l'abonnement du tenant est actif.
    Adapté pour les super admins.
    """
    # Pas de vérification pour les super admins
    if tenant is None:
        return None
    
    from app.models.user import User
    
    admin = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin"
    ).first()
    
    if not admin or not admin.subscription:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_subscription",
                "message": "Aucun abonnement trouvé pour ce tenant",
                "mode": "READ_ONLY"
            }
        )
    
    if not admin.subscription.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_expired",
                "message": "L'abonnement de ce tenant a expiré",
                "mode": "READ_ONLY",
                "expired_date": admin.subscription.end_date.isoformat() if admin.subscription.end_date else None
            }
        )
    
    return tenant


# =============================================================================
# 8. EXPORTS
# =============================================================================

__all__ = [
    # Authentification
    "get_db",
    "get_current_user",
    "get_current_active_user",
    "get_optional_current_user",
    "get_super_admin_user",
    "oauth2_scheme",
    
    # Tenant
    "get_current_tenant",
    "get_current_tenant_with_fallback",
    "get_tenant_id_from_request",
    
    # Abonnement
    "subscription_required", 
    "require_active_subscription",
    "check_admin_limits",
    
    # Pharmacie
    "get_current_pharmacy",
    "require_pharmacy_access",
    "get_pharmacy_or_main",
    "can_user_access_pharmacy",
    
    # Rôles et permissions
    "require_role",
    "require_permission",
    
    # Contexte combiné
    "get_authenticated_context",
    "get_current_user_with_context",
    
    # Utilitaires
    "get_pagination_params",
    "get_date_range_params"
]