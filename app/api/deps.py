"""
Dépendances FastAPI pour l'authentification, les autorisations et le contexte
multi-tenant de l'application SaaS Medigest.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import verify_token as security_verify_token
from app.db.session import get_db
from app.models.branch import Branch
from app.models.pharmacy import Pharmacy
from app.models.tenant import Tenant
from app.models.user import User
from app.services.subscription_service import (
    can_user_access_feature,
    check_tenant_limits,
    check_user_subscription,
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)

logger = logging.getLogger(__name__)


# =============================================================================
# OUTILS INTERNES
# =============================================================================

SUPER_ADMIN_ROLES = {"super_admin", "superadmin"}
ADMIN_OVERRIDE_ROLES = {"admin", "superviseur", "super_admin", "superadmin"}

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
        "settings:view", "settings:update",
        "branch:view", "branch:create", "branch:update", "branch:delete",
    ],
    "gerant": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel", "sales:stats",
        "pharmacy:view",
        "user:view",
        "report:view", "report:export",
        "branch:view",
    ],
    "pharmacien": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel",
        "report:view",
        "branch:view",
    ],
    "vendeur": [
        "stock:view",
        "sales:create", "sales:view",
    ],
    "caissier": [
        "sales:create", "sales:view", "sales:cancel",
    ],
    "superviseur": [
        "stock:view",
        "sales:view", "sales:stats", "sales:export",
        "pharmacy:view",
        "user:view",
        "report:view", "report:export",
        "branch:view",
    ],
    "technicien": [
        "stock:view", "stock:adjust",
    ],
}


def _is_super_admin(user: User) -> bool:
    return (getattr(user, "role", None) or "").lower() in SUPER_ADMIN_ROLES


def _decode_token_without_verification(token: str) -> Dict[str, Any]:
    """
    Décodage non vérifié du JWT pour lire le payload rapidement
    quand on a seulement besoin d'infos de contexte (ex: pharmacy_id).
    """
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"verify_signature": False},
        )
    except Exception:
        return {}


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _get_token_from_request(request: Request) -> Optional[str]:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[7:].strip() or None


# =============================================================================
# 1. AUTHENTIFICATION UTILISATEUR
# =============================================================================

def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """
    Récupère l'utilisateur courant à partir du token JWT.
    """
    if not token:
        logger.warning("❌ Aucun token trouvé dans les headers")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = security_verify_token(token)
        
        user_id: str = payload.get("sub")
        if not user_id:
            logger.error("❌ Payload invalide : champ 'sub' manquant")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide : identifiant manquant",
            )

        user = db.query(User).filter(User.id == user_id).first()
        
        if not user:
            logger.warning("❌ Utilisateur inexistant dans la DB : %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé",
            )

        if not getattr(user, "is_active", True):
            logger.warning("⚠️ Tentative de connexion sur compte désactivé : %s", user.email)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte inactif",
            )

        user.is_impersonated = bool(payload.get("is_impersonation", False))
        user.impersonated_by = payload.get("impersonated_by")
        user.jwt_payload = payload

        logger.info("✅ Authentification réussie : %s (ID: %s)", user.email, user.id)
        return user

    except JWTError as e:
        logger.warning("❌ Signature JWT invalide ou expirée : %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée ou invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.critical("🔥 Erreur système lors de l'authentification : %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur interne d'authentification",
        )


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Vérifie que l'utilisateur courant est actif et non verrouillé.
    """
    logger.info("🔐 Vérification utilisateur actif: %s", getattr(current_user, "email", None))

    locked_until = getattr(current_user, "locked_until", None)
    if locked_until and locked_until > datetime.utcnow():
        remaining = int((locked_until - datetime.utcnow()).total_seconds() / 60)
        logger.warning("⚠️ Compte verrouillé: %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Compte verrouillé. Réessayez dans {remaining} minutes.",
        )

    if not getattr(current_user, "is_active", True):
        logger.warning("⚠️ Compte désactivé: %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé",
        )

    return current_user


def get_optional_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """
    Retourne l'utilisateur courant si un token valide est fourni,
    sinon retourne None.
    """
    if not token:
        return None

    try:
        return get_current_user(db=db, token=token)
    except HTTPException:
        return None


def get_super_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Vérifie que l'utilisateur est super administrateur.
    """
    role = (getattr(current_user, "role", None) or "").lower()
    logger.info("🔐 Vérification super admin - Rôle: '%s'", role)

    if role not in SUPER_ADMIN_ROLES:
        logger.warning(
            "⛔ Accès super admin refusé pour %s (rôle: %s)",
            getattr(current_user, "email", None),
            role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "insufficient_role",
                "message": "Accès réservé aux super administrateurs",
                "current_role": role,
                "required_role": "super_admin",
            },
        )

    logger.info("✅ Accès super admin autorisé pour %s", getattr(current_user, "email", None))
    return current_user


# =============================================================================
# 2. CONTEXTE TENANT (MULTI-TENANT)
# =============================================================================

def get_current_tenant(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Récupère le tenant associé à l'utilisateur courant.
    Retourne None pour les super admins.
    """
    if _is_super_admin(current_user):
        logger.info("ℹ️ Super admin sans tenant: %s", getattr(current_user, "email", None))
        return None

    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        logger.warning("⚠️ Utilisateur sans tenant: %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Utilisateur non associé à un tenant",
        )

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        logger.error("❌ Tenant introuvable pour l'utilisateur %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant introuvable",
        )

    if tenant.status not in {"active", "trial"}:
        status_messages = {
            "suspended": "Votre compte a été suspendu. Contactez le support.",
            "expired": "Votre abonnement a expiré.",
            "cancelled": "Votre compte a été annulé.",
            "draft": "Votre compte n'est pas encore activé.",
            "archived": "Ce tenant est archivé.",
        }
        message = status_messages.get(tenant.status, f"Tenant {tenant.status}")
        logger.warning("⚠️ Tenant %s - %s", getattr(tenant, "tenant_code", None), message)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=message,
        )

    return tenant


def get_tenant_id_from_request(request: Request) -> Optional[str]:
    """
    Récupère un identifiant de tenant depuis la requête.
    """
    tenant_id = request.headers.get("X-Tenant-ID")
    if tenant_id:
        return tenant_id

    host = request.headers.get("host", "")
    if host and "." in host:
        subdomain = host.split(".")[0]
        if subdomain and subdomain not in {"www", "app", "api", "localhost"}:
            return subdomain

    state_tenant_id = getattr(request.state, "tenant_id", None)
    if state_tenant_id:
        return state_tenant_id

    return None


def get_current_tenant_with_fallback(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Récupère le tenant depuis :
    1. le profil utilisateur
    2. le header ou sous-domaine
    3. retourne None pour super admin
    """
    if getattr(current_user, "tenant_id", None):
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            return tenant

    if _is_super_admin(current_user):
        logger.info("ℹ️ Super admin sans tenant: %s", getattr(current_user, "email", None))
        return None

    tenant_id = get_tenant_id_from_request(request)
    if tenant_id:
        tenant_uuid = _parse_uuid(tenant_id)
        if tenant_uuid:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_uuid).first()
        else:
            tenant = db.query(Tenant).filter(
                (Tenant.tenant_code == tenant_id) | (Tenant.slug == tenant_id)
            ).first()

        if tenant:
            return tenant

    logger.warning("⚠️ Aucun tenant trouvé pour l'utilisateur %s", getattr(current_user, "email", None))
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Tenant non spécifié et non trouvé dans le profil utilisateur",
    )


# =============================================================================
# 3. ABONNEMENT ET LIMITES
# =============================================================================

async def require_active_subscription(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Vérifie que l'utilisateur a un abonnement actif.
    """
    if _is_super_admin(current_user):
        logger.info("✅ Super admin - accès illimité: %s", getattr(current_user, "email", None))
        return current_user

    sub_status = check_user_subscription(db, str(current_user.id))
    
    if not sub_status.get("has_subscription", False):
        logger.warning("⚠️ Pas d'abonnement pour %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_subscription",
                "message": "Vous n'avez pas d'abonnement actif",
                "mode": "READ_ONLY",
                "action": "subscribe",
            },
        )

    if sub_status.get("mode") == "READ_ONLY":
        error_detail = {
            "error": "subscription_expired",
            "message": sub_status.get("message", "Votre abonnement a expiré"),
            "mode": "READ_ONLY",
        }
        if sub_status.get("expired_date"):
            error_detail["expired_date"] = sub_status["expired_date"]

        logger.warning("⚠️ Abonnement expiré pour %s", getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=error_detail,
        )

    current_user.user_subscription_info = sub_status
    return current_user


async def check_admin_limits(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    action: Optional[str] = None,
) -> User:
    """
    Vérifie que l'admin peut effectuer une action selon les limites de son plan.
    """
    if getattr(current_user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action réservée aux administrateurs",
        )

    sub_status = check_user_subscription(db, str(current_user.id))
    if not sub_status.get("has_subscription", False) or sub_status.get("mode") == "READ_ONLY":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_required",
                "message": "Un abonnement actif est requis pour cette action",
                "mode": "READ_ONLY",
            },
        )

    limits = check_tenant_limits(db, str(current_user.tenant_id))
    current_user.tenant_limits = limits

    if action == "create_user" and not limits.get("can_create_user", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "user_limit_reached",
                "message": (
                    f"Limite d'utilisateurs atteinte "
                    f"({limits['current_usage']['users']} / {limits['limits']['max_users']})"
                ),
                "current": limits["current_usage"]["users"],
                "limit": limits["limits"]["max_users"],
                "suggestion": "Passez à un plan supérieur pour ajouter plus d'utilisateurs",
            },
        )

    if action == "create_pharmacy" and not limits.get("can_create_pharmacy", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "pharmacy_limit_reached",
                "message": (
                    f"Limite de pharmacies atteinte "
                    f"({limits['current_usage']['pharmacies']} / {limits['limits']['max_pharmacies']})"
                ),
                "current": limits["current_usage"]["pharmacies"],
                "limit": limits["limits"]["max_pharmacies"],
                "suggestion": "Passez à un plan supérieur pour créer plus de pharmacies",
            },
        )

    if action == "create_branch" and not limits.get("can_create_branch", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "branch_limit_reached",
                "message": (
                    f"Limite de succursales atteinte "
                    f"({limits['current_usage']['branches']} / {limits['limits']['max_branches']})"
                ),
                "current": limits["current_usage"].get("branches", 0),
                "limit": limits["limits"].get("max_branches", 0),
                "suggestion": "Passez à un plan supérieur pour créer plus de succursales",
            },
        )

    if action and not can_user_access_feature(current_user, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "feature_not_available",
                "message": "Cette fonctionnalité n'est pas disponible dans votre plan actuel",
                "feature": action,
                "plan": limits.get("plan"),
            },
        )

    return current_user


# =============================================================================
# 4. CONTEXTE PHARMACIE
# =============================================================================

def can_user_access_pharmacy(
    user: User,
    pharmacy: Pharmacy,
    db: Session,
) -> bool:
    """
    Vérifie si un utilisateur a accès à une pharmacie spécifique.
    """
    if _is_super_admin(user):
        return True

    if getattr(user, "role", None) == "admin" and getattr(user, "tenant_id", None) == getattr(pharmacy, "tenant_id", None):
        return True

    from app.models.user_pharmacy import UserPharmacy

    association = db.query(UserPharmacy).filter(
        UserPharmacy.user_id == user.id,
        UserPharmacy.pharmacy_id == pharmacy.id,
    ).first()

    return association is not None


def can_user_access_branch(
    user: User,
    branch: Branch,
    db: Session,
) -> bool:
    """
    Vérifie si un utilisateur a accès à une succursale spécifique.
    """
    if _is_super_admin(user):
        return True

    if getattr(user, "role", None) == "admin":
        return True

    # Vérifier via la pharmacie parente
    if branch.parent_pharmacy_id:
        from app.models.user_pharmacy import UserPharmacy
        association = db.query(UserPharmacy).filter(
            UserPharmacy.user_id == user.id,
            UserPharmacy.pharmacy_id == branch.parent_pharmacy_id,
        ).first()
        return association is not None

    return False


def get_current_pharmacy_entity(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Retourne l'entité SQLAlchemy Pharmacy courante.

    Ordre de recherche :
    1. header X-Pharmacy-ID
    2. pharmacy_id du JWT
    3. pharmacie principale du tenant
    4. première pharmacie active du tenant
    """
    header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
    pharmacy_uuid = _parse_uuid(header_pharmacy_id)

    if _is_super_admin(current_user):
        if pharmacy_uuid:
            return db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant introuvable pour la pharmacie courante",
        )

    if not pharmacy_uuid:
        token = _get_token_from_request(request)
        if token:
            payload = _decode_token_without_verification(token)
            pharmacy_uuid = _parse_uuid(payload.get("pharmacy_id"))

    if pharmacy_uuid:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_uuid,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

        if pharmacy:
            if not can_user_access_pharmacy(current_user, pharmacy, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Vous n'avez pas accès à cette pharmacie",
                )
            return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()
    if pharmacy:
        return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
    ).first()
    if pharmacy:
        return pharmacy

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Aucune pharmacie active trouvée pour ce tenant",
    )


def get_current_branch_entity(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    db: Session = Depends(get_db),
) -> Optional[Branch]:
    """
    Retourne l'entité SQLAlchemy Branch courante.

    Ordre de recherche :
    1. header X-Branch-ID
    2. branch_id du JWT
    3. première succursale active de la pharmacie courante
    """
    header_branch_id = request.headers.get("X-Branch-ID")
    branch_uuid = _parse_uuid(header_branch_id)

    if _is_super_admin(current_user):
        if branch_uuid:
            return db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant introuvable pour la succursale courante",
        )

    if not branch_uuid:
        token = _get_token_from_request(request)
        if token:
            payload = _decode_token_without_verification(token)
            branch_uuid = _parse_uuid(payload.get("branch_id"))

    if branch_uuid:
        branch = db.query(Branch).filter(
            Branch.id == branch_uuid,
            Branch.tenant_id == current_tenant.id,
            Branch.is_active.is_(True),
        ).first()

        if branch:
            if not can_user_access_branch(current_user, branch, db):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Vous n'avez pas accès à cette succursale",
                )
            return branch

    if current_pharmacy:
        branch = db.query(Branch).filter(
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
            Branch.is_main_branch.is_(True),
        ).first()
        if branch:
            return branch

        branch = db.query(Branch).filter(
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
        ).first()
        if branch:
            return branch

    return None


def get_current_pharmacy(
    pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la pharmacie courante sous forme sérialisée.
    """
    if _is_super_admin(current_user) and pharmacy is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }

    if not pharmacy:
        return None

    return {
        "id": str(pharmacy.id),
        "name": getattr(pharmacy, "name", None),
        "license_number": getattr(pharmacy, "license_number", None),
        "pharmacy_code": getattr(pharmacy, "pharmacy_code", None),
        "address": getattr(pharmacy, "address", None),
        "city": getattr(pharmacy, "city", None),
        "country": getattr(pharmacy, "country", None),
        "phone": getattr(pharmacy, "phone", None),
        "email": getattr(pharmacy, "email", None),
        "is_main": getattr(pharmacy, "is_main", False),
        "is_active": getattr(pharmacy, "is_active", False),
        "config": getattr(pharmacy, "config", {}) or {},
    }


def get_current_branch(
    branch: Optional[Branch] = Depends(get_current_branch_entity),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la succursale courante sous forme sérialisée.
    """
    if _is_super_admin(current_user) and branch is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }

    if not branch:
        return None

    return {
        "id": str(branch.id),
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "email": branch.email,
        "is_main_branch": branch.is_main_branch,
        "is_active": branch.is_active,
        "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
        "config": branch.config or {},
    }


def require_pharmacy_access(
    allow_admin_override: bool = True,
    require_active: bool = True,
):
    """
    Dépendance factory pour s'assurer que l'utilisateur a accès à une pharmacie.
    """
    def pharmacy_checker(
        current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
        current_user: User = Depends(get_current_active_user),
    ) -> Dict[str, Any]:
        if not current_pharmacy:
            if allow_admin_override and (getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES):
                return {
                    "id": None,
                    "name": "Accès global",
                    "is_global": True,
                    "role": current_user.role,
                }

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "pharmacy_required",
                    "message": "Une pharmacie doit être sélectionnée pour cette opération",
                },
            )

        if require_active and current_pharmacy.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "pharmacy_inactive",
                    "message": "La pharmacie sélectionnée est inactive",
                },
            )

        return current_pharmacy

    return pharmacy_checker


def require_branch_access(
    allow_admin_override: bool = True,
    require_active: bool = True,
):
    """
    Dépendance factory pour s'assurer que l'utilisateur a accès à une succursale.
    """
    def branch_checker(
        current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
        current_user: User = Depends(get_current_active_user),
    ) -> Dict[str, Any]:
        if not current_branch:
            if allow_admin_override and (getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES):
                return {
                    "id": None,
                    "name": "Accès global",
                    "is_global": True,
                    "role": current_user.role,
                }

            return None

        if require_active and current_branch.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "branch_inactive",
                    "message": "La succursale sélectionnée est inactive",
                },
            )

        return current_branch

    return branch_checker


def get_pharmacy_or_main(
    pharmacy_id: Optional[UUID] = None,
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Récupère une pharmacie spécifique ou la pharmacie principale du tenant.
    """
    if _is_super_admin(current_user):
        if pharmacy_id:
            return db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.is_active.is_(True),
            ).first()
        return None

    if not current_tenant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant non trouvé",
        )

    if pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "pharmacy_not_found",
                    "message": "Pharmacie non trouvée ou inactive",
                },
            )

        if not can_user_access_pharmacy(current_user, pharmacy, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "access_denied",
                    "message": "Vous n'avez pas accès à cette pharmacie",
                },
            )

        return pharmacy

    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()

    if not pharmacy:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()

    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "no_pharmacy",
                "message": "Aucune pharmacie active trouvée pour ce tenant",
            },
        )

    return pharmacy


# =============================================================================
# 5. RÔLES ET PERMISSIONS
# =============================================================================

def require_role(required_roles: List[str]):
    """
    Dépendance factory pour vérifier le rôle de l'utilisateur.
    """
    def role_checker(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if getattr(current_user, "role", None) not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "insufficient_role",
                    "message": f"Rôle requis : {' ou '.join(required_roles)}",
                    "current_role": getattr(current_user, "role", None),
                    "required_roles": required_roles,
                },
            )
        return current_user

    return role_checker


def require_permission(permission: str) -> Callable:
    """
    Dépendance factory pour vérifier une permission spécifique.
    Retourne une fonction de dépendance qui prend l'utilisateur courant en paramètre.
    """
    def permission_dependency(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        """
        Vérifie si l'utilisateur a la permission requise.
        """
        if _is_super_admin(current_user):
            logger.debug("✅ Super admin - accès illimité pour %s", permission)
            return current_user

        user_role = getattr(current_user, "role", None)
        user_permissions = PERMISSION_MAP.get(user_role, [])

        logger.debug(
            "🔐 Vérification permission - Rôle: %s, Permission: %s",
            user_role,
            permission,
        )

        if permission not in user_permissions and "*" not in user_permissions:
            logger.warning(
                "⛔ Permission refusée - Rôle: %s, Permission requise: %s",
                user_role,
                permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "permission_denied",
                    "message": f"Permission requise : {permission}",
                    "current_role": user_role,
                    "required_permission": permission,
                },
            )

        logger.debug("✅ Permission accordée: %s", permission)
        return current_user

    return permission_dependency


# =============================================================================
# 6. CONTEXTE COMBINÉ
# =============================================================================

def get_authenticated_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
    current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Contexte d'authentification complet.
    """
    sub_status = None
    if not _is_super_admin(current_user):
        sub_status = check_user_subscription(db, str(current_user.id))

    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "branch": current_branch,
        "subscription": sub_status,
        "db": db,
    }


def get_current_user_with_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
    current_branch: Optional[Dict[str, Any]] = Depends(get_current_branch),
) -> Dict[str, Any]:
    """
    Retourne un contexte utilisateur enrichi.
    """
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "branch": current_branch,
        "has_pharmacy_access": current_pharmacy is not None,
        "has_branch_access": current_branch is not None,
        "role": getattr(current_user, "role", None),
        "is_super_admin": _is_super_admin(current_user),
    }


# =============================================================================
# 7. UTILITAIRES
# =============================================================================

def get_pagination_params(
    page: int = 1,
    limit: int = 20,
) -> Dict[str, int]:
    """
    Valide et retourne les paramètres de pagination.
    """
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le numéro de page doit être >= 1",
        )

    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La limite doit être comprise entre 1 et 100",
        )

    return {
        "page": page,
        "limit": limit,
        "skip": (page - 1) * limit,
    }


def get_date_range_params(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Optional[datetime]]:
    """
    Valide et retourne les paramètres de plage de dates.
    """
    result: Dict[str, Optional[datetime]] = {"start_date": None, "end_date": None}

    if start_date:
        try:
            result["start_date"] = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de début invalide. Utilisez ISO format (YYYY-MM-DD)",
            )

    if end_date:
        try:
            result["end_date"] = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de date de fin invalide. Utilisez ISO format (YYYY-MM-DD)",
            )

    if result["start_date"] and result["end_date"] and result["start_date"] > result["end_date"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La date de début ne peut pas être postérieure à la date de fin",
        )

    return result


def subscription_required(
    tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Tenant]:
    """
    Vérifie que l'abonnement du tenant est actif.
    Adapté pour les super admins.
    """
    if tenant is None:
        return None

    admin = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin",
    ).first()

    if not admin or not getattr(admin, "subscription", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "no_subscription",
                "message": "Aucun abonnement trouvé pour ce tenant",
                "mode": "READ_ONLY",
            },
        )

    if not admin.subscription.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "subscription_expired",
                "message": "L'abonnement de ce tenant a expiré",
                "mode": "READ_ONLY",
                "expired_date": (
                    admin.subscription.end_date.isoformat()
                    if getattr(admin.subscription, "end_date", None)
                    else None
                ),
            },
        )

    return tenant

# =============================================================================
# 4.1 PHARMACIE ACTIVE (UTILISATEUR)
# =============================================================================

def get_current_active_pharmacy(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    db: Session = Depends(get_db),
) -> Optional[Pharmacy]:
    """
    Récupère la pharmacie active pour l'utilisateur connecté.
    
    Ordre de recherche :
    1. Header X-Pharmacy-ID
    2. active_pharmacy_id stocké dans l'utilisateur
    3. pharmacy_id du JWT
    4. Pharmacie principale du tenant
    5. Première pharmacie active du tenant
    
    Retourne l'entité Pharmacy SQLAlchemy.
    """
    # Super admin : accès global ou via header
    if _is_super_admin(current_user):
        header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
        if header_pharmacy_id:
            pharmacy_uuid = _parse_uuid(header_pharmacy_id)
            if pharmacy_uuid:
                pharmacy = db.query(Pharmacy).filter(
                    Pharmacy.id == pharmacy_uuid,
                    Pharmacy.is_active.is_(True),
                ).first()
                if pharmacy:
                    return pharmacy
        return None
    
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant:
        logger.warning("⚠️ Utilisateur sans tenant pour get_current_active_pharmacy: %s", 
                       getattr(current_user, "email", None))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant non trouvé",
        )
    
    # 1. Vérifier le header X-Pharmacy-ID
    header_pharmacy_id = request.headers.get("X-Pharmacy-ID")
    if header_pharmacy_id:
        pharmacy_uuid = _parse_uuid(header_pharmacy_id)
        if pharmacy_uuid:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active.is_(True),
            ).first()
            
            if pharmacy:
                if not can_user_access_pharmacy(current_user, pharmacy, db):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Vous n'avez pas accès à cette pharmacie",
                    )
                logger.debug("✅ Pharmacie active via header X-Pharmacy-ID: %s", pharmacy.id)
                return pharmacy
    
    # 2. Vérifier l'active_pharmacy_id stocké dans l'utilisateur
    active_pharmacy_id = getattr(current_user, "active_pharmacy_id", None)
    if active_pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == active_pharmacy_id,
            Pharmacy.tenant_id == current_tenant.id,
            Pharmacy.is_active.is_(True),
        ).first()
        
        if pharmacy:
            if not can_user_access_pharmacy(current_user, pharmacy, db):
                logger.warning("⚠️ Utilisateur %s n'a plus accès à sa pharmacie active %s",
                              getattr(current_user, "email", None), active_pharmacy_id)
                # Réinitialiser l'active_pharmacy_id
                current_user.active_pharmacy_id = None
                db.commit()
            else:
                logger.debug("✅ Pharmacie active via user.active_pharmacy_id: %s", pharmacy.id)
                return pharmacy
    
    # 3. Vérifier le pharmacy_id du JWT
    token = _get_token_from_request(request)
    if token:
        payload = _decode_token_without_verification(token)
        pharmacy_uuid = _parse_uuid(payload.get("pharmacy_id"))
        if pharmacy_uuid:
            pharmacy = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_uuid,
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active.is_(True),
            ).first()
            
            if pharmacy:
                if not can_user_access_pharmacy(current_user, pharmacy, db):
                    logger.warning("⚠️ Utilisateur %s n'a pas accès à pharmacy_id du JWT",
                                  getattr(current_user, "email", None))
                else:
                    # Mettre à jour l'active_pharmacy_id de l'utilisateur
                    current_user.active_pharmacy_id = pharmacy.id
                    db.commit()
                    logger.debug("✅ Pharmacie active via JWT pharmacy_id: %s", pharmacy.id)
                    return pharmacy
    
    # 4. Pharmacie principale du tenant
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
        Pharmacy.is_main.is_(True),
    ).first()
    
    if pharmacy:
        if can_user_access_pharmacy(current_user, pharmacy, db):
            # Mettre à jour l'active_pharmacy_id de l'utilisateur
            current_user.active_pharmacy_id = pharmacy.id
            db.commit()
            logger.debug("✅ Pharmacie active via is_main: %s", pharmacy.id)
            return pharmacy
    
    # 5. Première pharmacie active du tenant
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_tenant.id,
        Pharmacy.is_active.is_(True),
    ).first()
    
    if pharmacy:
        if can_user_access_pharmacy(current_user, pharmacy, db):
            # Mettre à jour l'active_pharmacy_id de l'utilisateur
            current_user.active_pharmacy_id = pharmacy.id
            db.commit()
            logger.debug("✅ Pharmacie active via first active: %s", pharmacy.id)
            return pharmacy
    
    # Aucune pharmacie trouvée
    logger.warning("⚠️ Aucune pharmacie active trouvée pour tenant %s, utilisateur %s",
                   getattr(current_tenant, "id", None), getattr(current_user, "email", None))
    
    # Pour les admins, on retourne None (peut-être qu'ils n'ont pas encore de pharmacie)
    if getattr(current_user, "role", None) in ADMIN_OVERRIDE_ROLES:
        return None
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "no_active_pharmacy",
            "message": "Aucune pharmacie active trouvée pour cet utilisateur",
            "suggestion": "Créez une pharmacie ou contactez votre administrateur",
        },
    )


def get_current_active_branch(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_active_pharmacy),
    db: Session = Depends(get_db),
) -> Optional[Branch]:
    """
    Récupère la succursale active pour l'utilisateur connecté.
    
    Ordre de recherche :
    1. Header X-Branch-ID
    2. active_branch_id stocké dans l'utilisateur
    3. branch_id du JWT
    4. Succursale principale de la pharmacie active
    5. Première succursale active de la pharmacie active
    
    Retourne l'entité Branch SQLAlchemy.
    """
    # Super admin : accès global ou via header
    if _is_super_admin(current_user):
        header_branch_id = request.headers.get("X-Branch-ID")
        if header_branch_id:
            branch_uuid = _parse_uuid(header_branch_id)
            if branch_uuid:
                branch = db.query(Branch).filter(
                    Branch.id == branch_uuid,
                    Branch.is_active.is_(True),
                ).first()
                if branch:
                    return branch
        return None
    
    # Vérifier que l'utilisateur a un tenant
    if not current_tenant:
        logger.warning("⚠️ Utilisateur sans tenant pour get_current_active_branch: %s",
                       getattr(current_user, "email", None))
        return None
    
    # Vérifier qu'on a une pharmacie active
    if not current_pharmacy:
        logger.debug("ℹ️ Aucune pharmacie active, impossible de déterminer la branche")
        return None
    
    # 1. Vérifier le header X-Branch-ID
    header_branch_id = request.headers.get("X-Branch-ID")
    if header_branch_id:
        branch_uuid = _parse_uuid(header_branch_id)
        if branch_uuid:
            branch = db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.tenant_id == current_tenant.id,
                Branch.parent_pharmacy_id == current_pharmacy.id,
                Branch.is_active.is_(True),
            ).first()
            
            if branch:
                if not can_user_access_branch(current_user, branch, db):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Vous n'avez pas accès à cette succursale",
                    )
                logger.debug("✅ Branche active via header X-Branch-ID: %s", branch.id)
                return branch
    
    # 2. Vérifier l'active_branch_id stocké dans l'utilisateur
    active_branch_id = getattr(current_user, "active_branch_id", None)
    if active_branch_id:
        branch = db.query(Branch).filter(
            Branch.id == active_branch_id,
            Branch.tenant_id == current_tenant.id,
            Branch.parent_pharmacy_id == current_pharmacy.id,
            Branch.is_active.is_(True),
        ).first()
        
        if branch:
            if not can_user_access_branch(current_user, branch, db):
                logger.warning("⚠️ Utilisateur %s n'a plus accès à sa branche active %s",
                              getattr(current_user, "email", None), active_branch_id)
                # Réinitialiser l'active_branch_id
                current_user.active_branch_id = None
                db.commit()
            else:
                logger.debug("✅ Branche active via user.active_branch_id: %s", branch.id)
                return branch
    
    # 3. Vérifier le branch_id du JWT
    token = _get_token_from_request(request)
    if token:
        payload = _decode_token_without_verification(token)
        branch_uuid = _parse_uuid(payload.get("branch_id"))
        if branch_uuid:
            branch = db.query(Branch).filter(
                Branch.id == branch_uuid,
                Branch.tenant_id == current_tenant.id,
                Branch.parent_pharmacy_id == current_pharmacy.id,
                Branch.is_active.is_(True),
            ).first()
            
            if branch:
                if not can_user_access_branch(current_user, branch, db):
                    logger.warning("⚠️ Utilisateur %s n'a pas accès à branch_id du JWT",
                                  getattr(current_user, "email", None))
                else:
                    # Mettre à jour l'active_branch_id de l'utilisateur
                    current_user.active_branch_id = branch.id
                    db.commit()
                    logger.debug("✅ Branche active via JWT branch_id: %s", branch.id)
                    return branch
    
    # 4. Succursale principale de la pharmacie active
    branch = db.query(Branch).filter(
        Branch.tenant_id == current_tenant.id,
        Branch.parent_pharmacy_id == current_pharmacy.id,
        Branch.is_active.is_(True),
        Branch.is_main_branch.is_(True),
    ).first()
    
    if branch:
        if can_user_access_branch(current_user, branch, db):
            # Mettre à jour l'active_branch_id de l'utilisateur
            current_user.active_branch_id = branch.id
            db.commit()
            logger.debug("✅ Branche active via is_main_branch: %s", branch.id)
            return branch
    
    # 5. Première succursale active de la pharmacie active
    branch = db.query(Branch).filter(
        Branch.tenant_id == current_tenant.id,
        Branch.parent_pharmacy_id == current_pharmacy.id,
        Branch.is_active.is_(True),
    ).first()
    
    if branch:
        if can_user_access_branch(current_user, branch, db):
            # Mettre à jour l'active_branch_id de l'utilisateur
            current_user.active_branch_id = branch.id
            db.commit()
            logger.debug("✅ Branche active via first active: %s", branch.id)
            return branch
    
    # Aucune branche trouvée - ce n'est pas une erreur, certaines pharmacies n'ont pas de branches
    logger.debug("ℹ️ Aucune branche active trouvée pour pharmacie %s", current_pharmacy.id)
    return None


def get_current_active_pharmacy_dict(
    pharmacy: Optional[Pharmacy] = Depends(get_current_active_pharmacy),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la pharmacie active sous forme sérialisée (dictionnaire).
    """
    if _is_super_admin(current_user) and pharmacy is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }
    
    if not pharmacy:
        return None
    
    return {
        "id": str(pharmacy.id),
        "name": getattr(pharmacy, "name", None),
        "license_number": getattr(pharmacy, "license_number", None),
        "pharmacy_code": getattr(pharmacy, "pharmacy_code", None),
        "address": getattr(pharmacy, "address", None),
        "city": getattr(pharmacy, "city", None),
        "country": getattr(pharmacy, "country", None),
        "phone": getattr(pharmacy, "phone", None),
        "email": getattr(pharmacy, "email", None),
        "is_main": getattr(pharmacy, "is_main", False),
        "is_active": getattr(pharmacy, "is_active", False),
        "config": getattr(pharmacy, "config", {}) or {},
    }


def get_current_active_branch_dict(
    branch: Optional[Branch] = Depends(get_current_active_branch),
    current_user: User = Depends(get_current_active_user),
) -> Optional[Dict[str, Any]]:
    """
    Retourne la succursale active sous forme sérialisée (dictionnaire).
    """
    if _is_super_admin(current_user) and branch is None:
        return {
            "id": None,
            "name": "Accès super admin",
            "is_global": True,
            "role": current_user.role,
        }
    
    if not branch:
        return None
    
    return {
        "id": str(branch.id),
        "name": branch.name,
        "code": branch.code,
        "address": branch.address,
        "city": branch.city,
        "phone": branch.phone,
        "email": branch.email,
        "is_main_branch": branch.is_main_branch,
        "is_active": branch.is_active,
        "parent_pharmacy_id": str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
        "config": branch.config or {},
    }


# =============================================================================
# 8. EXPORTS
# =============================================================================

__all__ = [
    "get_db",
    "oauth2_scheme",
    "get_current_user",
    "get_current_active_user",
    "get_optional_current_user",
    "get_super_admin_user",
    "get_current_tenant",
    "get_current_tenant_with_fallback",
    "get_tenant_id_from_request",
    "require_active_subscription",
    "check_admin_limits",
    "get_current_pharmacy_entity",
    "get_current_branch_entity",
    "get_current_pharmacy",
    "get_current_branch",
    "require_pharmacy_access",
    "require_branch_access",
    "get_pharmacy_or_main",
    "can_user_access_pharmacy",
    "can_user_access_branch",
    "require_role",
    "require_permission",
    "get_authenticated_context",
    "get_current_user_with_context",
    "get_pagination_params",
    "get_date_range_params",
    "subscription_required",
    "get_current_active_pharmacy",
    "get_current_active_branch",
    "get_current_active_pharmacy_dict",
    "get_current_active_branch_dict",
]