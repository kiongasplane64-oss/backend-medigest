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
    ],
    "gerant": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel", "sales:stats",
        "pharmacy:view",
        "user:view",
        "report:view", "report:export",
    ],
    "pharmacien": [
        "stock:view", "stock:create", "stock:update", "stock:adjust",
        "sales:view", "sales:create", "sales:cancel",
        "report:view",
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
    token: Optional[str] = Depends(oauth2_scheme),
) -> User:
    """
    Récupère l'utilisateur courant à partir du token JWT.
    """
    logger.info("🔐 get_current_user - Token présent: %s", bool(token))

    if not token:
        logger.warning("❌ Token d'authentification manquant")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token d'authentification manquant",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_preview = f"{token[:15]}...{token[-10:]}" if len(token) > 25 else token
        logger.debug("🔑 Validation du token: %s", token_preview)

        payload = security_verify_token(token)
        logger.info("📦 Payload décodé: %s", payload)

        user_id = payload.get("sub")
        if not user_id:
            logger.warning("❌ Token invalide : aucun identifiant utilisateur")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalide : aucun identifiant utilisateur",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            logger.warning("❌ Utilisateur non trouvé pour l'ID: %s", user_id)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Utilisateur non trouvé",
            )

        logger.info(
            "✅ Utilisateur trouvé: %s, rôle: %s, actif: %s",
            getattr(user, "email", None),
            getattr(user, "role", None),
            getattr(user, "actif", None),
        )

        if not getattr(user, "actif", False):
            logger.warning("⚠️ Compte désactivé: %s", getattr(user, "email", None))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte désactivé",
            )

        user.is_impersonated = bool(payload.get("is_impersonation", False))
        user.impersonated_by = payload.get("impersonated_by")
        user.jwt_payload = payload

        return user

    except HTTPException:
        raise
    except JWTError as exc:
        logger.warning("❌ Erreur JWT: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as exc:
        logger.error("❌ Erreur inattendue: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Erreur d'authentification",
            headers={"WWW-Authenticate": "Bearer"},
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

    if not getattr(current_user, "actif", False):
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

    current_user.subscription_info = sub_status
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

        # Vérifier si l'utilisateur a la permission spécifique ou "*"
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
        "subscription": sub_status,
        "db": db,
    }


def get_current_user_with_context(
    current_user: User = Depends(get_current_active_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Dict[str, Any]] = Depends(get_current_pharmacy),
) -> Dict[str, Any]:
    """
    Retourne un contexte utilisateur enrichi.
    """
    return {
        "user": current_user,
        "tenant": current_tenant,
        "pharmacy": current_pharmacy,
        "has_pharmacy_access": current_pharmacy is not None,
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
    "get_current_pharmacy",
    "require_pharmacy_access",
    "get_pharmacy_or_main",
    "can_user_access_pharmacy",
    "require_role",
    "require_permission",
    "get_authenticated_context",
    "get_current_user_with_context",
    "get_pagination_params",
    "get_date_range_params",
    "subscription_required",
]