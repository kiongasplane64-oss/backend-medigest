# app/api/v1/subscriptions.py
"""
Endpoints de gestion des abonnements.
- Utilisateur connecté
- Admin tenant
- Super admin
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user
from app.models.user import User
from app.schemas.subscription import UpgradeSubscriptionSchema, ManualActivationSchema
from app.services.subscription_service import (
    PLAN_CONFIG,
    check_tenant_limits,
    check_user_subscription,
    create_trial_subscription,
    get_subscription_summary_for_superadmin,
    upgrade_subscription,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])


# =============================================================================
# HELPERS
# =============================================================================

def utc_now_iso() -> str:
    return datetime.utcnow().isoformat()


def to_str_uuid(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def safe_percentage(current: int, limit: int) -> float:
    if not limit or limit <= 0:
        return 0.0
    return round((current / limit) * 100, 2)


def get_access_mode(subscription_status: Dict[str, Any]) -> str:
    return "full" if subscription_status.get("is_active", False) else "read_only"


def get_read_only_restrictions() -> Dict[str, Any]:
    return {
        "can_view": True,
        "can_create": False,
        "can_update": False,
        "can_delete": False,
        "can_export": True,
        "max_items_visible": 100,
        "message": "Mode lecture seule : vous pouvez consulter les données mais pas les modifier.",
    }


def build_plan_payload(plan_key: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": plan_key,
        "name": config.get("name", plan_key.title()),
        "price_monthly": config.get("price_monthly", 0),
        "price_yearly": config.get("price_yearly", 0),
        "max_users": (
            config.get("max_users_per_tenant")
            if config.get("max_users_per_tenant", 0) > 0
            else "Illimité"
        ),
        "max_products": (
            config.get("max_products")
            if config.get("max_products", 0) > 0
            else "Illimité"
        ),
        "max_pharmacies": (
            config.get("max_pharmacies")
            if config.get("max_pharmacies", 0) > 0
            else "Illimité"
        ),
        "features": config.get("features", []),
        "is_trial": plan_key == "trial",
    }


def build_subscription_summary_for_user(
    db: Session,
    current_user: User,
    include_usage: bool = False,
) -> Dict[str, Any]:
    """
    Construit une réponse détaillée et cohérente sur l'abonnement courant.
    """
    sub_status = check_user_subscription(db, str(current_user.id))
    access_mode = get_access_mode(sub_status)
    is_read_only = access_mode == "read_only"

    if not sub_status.get("has_subscription", False):
        sub_status["message"] = "Aucun abonnement actif. Accès limité en lecture seule."
        access_mode = "read_only"
        is_read_only = True

    plan_type = sub_status.get("plan", "unknown")
    plan_config = PLAN_CONFIG.get(plan_type, {})

    current_users_count = 0
    current_products_count = 0
    current_pharmacies_count = 0

    if include_usage and current_user.tenant_id:
        try:
            from app.models.product import Product
            from app.models.pharmacy import Pharmacy

            current_users_count = db.query(User).filter(
                User.tenant_id == current_user.tenant_id,
                User.actif.is_(True),
            ).count()

            current_products_count = db.query(Product).filter(
                Product.tenant_id == current_user.tenant_id
            ).count()

            current_pharmacies_count = db.query(Pharmacy).filter(
                Pharmacy.tenant_id == current_user.tenant_id
            ).count()

        except Exception as exc:
            logger.error("Erreur lors du calcul d'usage abonnement: %s", exc, exc_info=True)

    response = {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role,
            "tenant_id": to_str_uuid(current_user.tenant_id),
        },
        "subscription": {
            "has_subscription": sub_status.get("has_subscription", False),
            "is_active": sub_status.get("is_active", False),
            "plan": plan_type,
            "plan_name": plan_config.get("name", "Inconnu"),
            "status": sub_status.get("status", "none"),
            "start_date": sub_status.get("start_date"),
            "end_date": sub_status.get("end_date"),
            "days_remaining": sub_status.get("days_remaining", 0),
            "trial_end_date": sub_status.get("trial_end_date"),
            "is_trial": plan_type == "trial",
            "auto_renew": sub_status.get("auto_renew", True),
            "billing_cycle": sub_status.get("billing_cycle", "monthly"),
            "price_monthly": float(plan_config.get("price_monthly", 0) or 0),
            "price_yearly": float(plan_config.get("price_yearly", 0) or 0),
        },
        "access": {
            "mode": access_mode,
            "is_read_only": is_read_only,
            "restrictions": get_read_only_restrictions() if is_read_only else None,
        },
        "limits": {
            "max_users": plan_config.get("max_users_per_tenant", 0),
            "max_products": plan_config.get("max_products", 0),
            "max_pharmacies": plan_config.get("max_pharmacies", 0),
            "features": plan_config.get("features", []),
        },
        "usage": {
            "current_users": current_users_count,
            "current_products": current_products_count,
            "current_pharmacies": current_pharmacies_count,
            "users_percentage": safe_percentage(
                current_users_count, int(plan_config.get("max_users_per_tenant", 0) or 0)
            ),
            "products_percentage": safe_percentage(
                current_products_count, int(plan_config.get("max_products", 0) or 0)
            ),
            "pharmacies_percentage": safe_percentage(
                current_pharmacies_count, int(plan_config.get("max_pharmacies", 0) or 0)
            ),
        } if include_usage else {},
        "metadata": {
            "checked_at": utc_now_iso(),
            "requires_upgrade": is_read_only and sub_status.get("has_subscription", False),
            "requires_subscription": not sub_status.get("has_subscription", False),
        },
    }

    if current_user.role == "admin" and current_user.tenant_id and sub_status.get("is_active", False):
        try:
            response["tenant_limits"] = check_tenant_limits(db, str(current_user.tenant_id))
        except Exception as exc:
            logger.error("Erreur vérification limites tenant: %s", exc, exc_info=True)
            response["tenant_limits"] = {"error": "limits_check_failed"}

    return response


# =============================================================================
# ENDPOINTS UTILISATEUR
# =============================================================================

@router.get("/my-status", response_model=Dict[str, Any])
async def get_my_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Statut rapide de l'abonnement de l'utilisateur connecté.
    """
    logger.info("Vérification du statut abonnement pour %s", current_user.email)

    data = build_subscription_summary_for_user(
        db=db,
        current_user=current_user,
        include_usage=False,
    )

    return {
        **data["subscription"],
        "access_mode": data["access"]["mode"],
        "is_read_only": data["access"]["is_read_only"],
        "restrictions": data["access"]["restrictions"],
        "user": data["user"],
        "tenant_limits": data.get("tenant_limits"),
        "metadata": data["metadata"],
    }


@router.get("/plans", response_model=Dict[str, List[Dict[str, Any]]])
async def get_available_plans(
    include_trial: bool = Query(False),
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Liste des plans disponibles.
    """
    plans: List[Dict[str, Any]] = []

    for key, config in PLAN_CONFIG.items():
        if key == "trial" and not include_trial:
            continue
        plans.append(build_plan_payload(key, config))

    logger.info("Liste des plans récupérée: %s plans", len(plans))
    return {"plans": plans}


@router.post("/upgrade", response_model=Dict[str, Any])
async def upgrade_my_subscription(
    data: UpgradeSubscriptionSchema,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Met à niveau l'abonnement d'un tenant.
    Réservé à l'admin du tenant.
    """
    if current_user.role != "admin":
        logger.warning("Tentative de changement abonnement par non-admin: %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "unauthorized",
                "message": "Seuls les administrateurs peuvent changer d'abonnement.",
            },
        )

    logger.info(
        "Demande upgrade abonnement par %s vers le plan %s",
        current_user.email,
        data.plan,
    )

    try:
        subscription = upgrade_subscription(
            db=db,
            user_id=current_user.id,
            new_plan=data.plan,
            billing_cycle=data.billing_cycle,
            payment_id=data.payment_id,
            payment_method=data.payment_method,
        )

        return {
            "success": True,
            "message": "Abonnement mis à niveau avec succès.",
            "subscription": {
                "id": str(subscription.id),
                "user_id": str(subscription.user_id),
                "tenant_id": to_str_uuid(subscription.tenant_id),
                "plan": subscription.plan_type,
                "plan_name": subscription.plan_name,
                "status": subscription.status,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining(),
                "price": float(subscription.price or 0),
                "billing_cycle": subscription.billing_cycle,
                "currency": getattr(subscription, "currency", "USD"),
            },
            "access_mode": "full",
            "upgraded_at": utc_now_iso(),
        }

    except ValueError as exc:
        logger.error("Erreur validation upgrade: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_request",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Erreur inattendue upgrade: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "upgrade_failed",
                "message": "Erreur lors de la mise à niveau. Veuillez réessayer.",
            },
        )


@router.get("/check-access/{feature}", response_model=Dict[str, Any])
async def check_feature_access(
    feature: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Vérifie l'accès à une fonctionnalité.
    """
    from app.services.subscription_service import can_user_access_feature

    sub_status = check_user_subscription(db, str(current_user.id))
    is_read_only = not sub_status.get("is_active", False)

    write_keywords = {"create", "update", "delete", "edit", "add", "remove", "modify"}
    feature_lower = feature.lower()
    is_write_operation = any(keyword in feature_lower for keyword in write_keywords)

    if is_read_only and is_write_operation:
        has_access = False
        denied_reason = "Opération non autorisée en mode lecture seule."
    else:
        has_access = can_user_access_feature(current_user, feature)
        denied_reason = None if has_access else "Fonctionnalité non incluse dans votre plan."

    return {
        "feature": feature,
        "has_access": has_access,
        "subscription_active": sub_status.get("is_active", False),
        "has_subscription": sub_status.get("has_subscription", False),
        "plan": sub_status.get("plan", "unknown"),
        "mode": "READ_ONLY" if is_read_only else "FULL",
        "is_read_only": is_read_only,
        "access_denied_reason": denied_reason,
        "requires_upgrade": is_read_only and is_write_operation,
        "checked_at": utc_now_iso(),
    }


@router.get("/status", response_model=Dict[str, Any])
async def get_subscription_status(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """
    Statut détaillé de l'abonnement.
    """
    logger.info("Demande du statut détaillé abonnement pour %s", current_user.email)

    return build_subscription_summary_for_user(
        db=db,
        current_user=current_user,
        include_usage=True,
    )


@router.get("/usage", response_model=Dict[str, Any])
async def get_subscription_usage(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    detailed: bool = Query(False),
) -> Dict[str, Any]:
    """
    Statistiques d'utilisation des ressources par rapport au plan.
    """
    logger.info("Demande des statistiques d'utilisation pour %s", current_user.email)

    sub_status = check_user_subscription(db, str(current_user.id))

    if not sub_status.get("has_subscription", False):
        return {
            "has_subscription": False,
            "subscription_active": False,
            "message": "Aucun abonnement actif.",
            "access_mode": "read_only",
            "usage": {},
            "limits": {},
            "percentages": {},
            "alerts": [],
            "timestamp": utc_now_iso(),
        }

    plan_type = sub_status.get("plan", "unknown")
    plan_config = PLAN_CONFIG.get(plan_type, {})

    payload: Dict[str, Any] = {
        "has_subscription": True,
        "subscription_active": sub_status.get("is_active", False),
        "plan": plan_type,
        "plan_name": plan_config.get("name", "Inconnu"),
        "access_mode": "full" if sub_status.get("is_active", False) else "read_only",
        "usage": {},
        "limits": {},
        "percentages": {},
        "alerts": [],
        "timestamp": utc_now_iso(),
    }

    if not current_user.tenant_id or not sub_status.get("is_active", False):
        payload["message"] = "Abonnement inactif ou tenant absent : limites non applicables."
        return payload

    try:
        from app.models.inventory import Inventory
        from app.models.pharmacy import Pharmacy
        from app.models.product import Product
        from app.models.sale import Sale

        tenant_id = current_user.tenant_id
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)

        users = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.actif.is_(True),
        ).all()
        users_count = len(users)

        products_count = db.query(Product).filter(
            Product.tenant_id == tenant_id
        ).count()

        pharmacies_query = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == tenant_id
        )
        pharmacies_count = pharmacies_query.count()

        sales_count = db.query(Sale).filter(
            Sale.tenant_id == tenant_id,
            Sale.created_at >= thirty_days_ago,
        ).count()

        low_stock_count = db.query(Inventory).filter(
            Inventory.tenant_id == tenant_id,
            Inventory.quantity <= Inventory.min_threshold,
        ).count()

        users_limit = int(plan_config.get("max_users_per_tenant", 0) or 0)
        products_limit = int(plan_config.get("max_products", 0) or 0)
        pharmacies_limit = int(plan_config.get("max_pharmacies", 0) or 0)

        users_percentage = safe_percentage(users_count, users_limit)
        products_percentage = safe_percentage(products_count, products_limit)
        pharmacies_percentage = safe_percentage(pharmacies_count, pharmacies_limit)

        payload["usage"] = {
            "users": users_count,
            "products": products_count,
            "pharmacies": pharmacies_count,
            "sales_last_30_days": sales_count,
            "low_stock_items": low_stock_count,
        }

        payload["limits"] = {
            "users": users_limit if users_limit > 0 else "Illimité",
            "products": products_limit if products_limit > 0 else "Illimité",
            "pharmacies": pharmacies_limit if pharmacies_limit > 0 else "Illimité",
        }

        payload["percentages"] = {
            "users": users_percentage,
            "products": products_percentage,
            "pharmacies": pharmacies_percentage,
        }

        for resource_name, current, limit, percentage in [
            ("users", users_count, users_limit, users_percentage),
            ("products", products_count, products_limit, products_percentage),
            ("pharmacies", pharmacies_count, pharmacies_limit, pharmacies_percentage),
        ]:
            if limit > 0 and percentage >= 80:
                payload["alerts"].append({
                    "type": f"{resource_name}_limit",
                    "severity": "critical" if percentage >= 95 else "warning",
                    "message": f"Vous utilisez {percentage}% de votre limite de {resource_name}.",
                    "current": current,
                    "limit": limit,
                    "percentage": percentage,
                })

        if detailed:
            users_by_role: Dict[str, int] = {}
            for user in users:
                users_by_role[user.role] = users_by_role.get(user.role, 0) + 1

            products_by_category: Dict[str, int] = {}
            rows = db.query(Product.category).filter(
                Product.tenant_id == tenant_id,
                Product.category.isnot(None),
            ).all()

            for row in rows:
                category_value = getattr(row, "category", None)
                if category_value:
                    products_by_category[category_value] = products_by_category.get(category_value, 0) + 1

            payload["details"] = {
                "users_by_role": users_by_role,
                "products_by_category": products_by_category,
                "total_categories_used": len(products_by_category),
                "pharmacies_list": [
                    {"id": str(ph.id), "name": ph.name}
                    for ph in pharmacies_query.limit(5).all()
                ],
            }

        if any(value >= 90 for value in [users_percentage, products_percentage, pharmacies_percentage]):
            payload["recommendations"] = {
                "message": "Vous approchez des limites de votre plan actuel.",
                "suggested_upgrade": "premium" if plan_type == "basic" else "enterprise",
                "upgrade_url": "/api/v1/subscriptions/upgrade",
            }

        return payload

    except Exception as exc:
        logger.error("Erreur calcul usage abonnement: %s", exc, exc_info=True)
        payload["error"] = "usage_fetch_failed"
        payload["message"] = "Erreur lors de la récupération des statistiques détaillées."
        return payload


# =============================================================================
# ENDPOINTS SUPER ADMIN
# =============================================================================

@router.get("/admin/overview", response_model=Dict[str, Any])
async def get_subscriptions_overview(
    tenant_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """
    Vue d'ensemble des abonnements pour le super admin.
    """
    logger.info("Vue d'ensemble abonnements demandée par %s", current_user.email)

    try:
        summary = get_subscription_summary_for_superadmin(db, tenant_id)
        summary["requested_by"] = current_user.email
        summary["requested_at"] = utc_now_iso()
        summary["filter"] = {"tenant_id": tenant_id} if tenant_id else None
        return summary

    except Exception as exc:
        logger.error("Erreur overview abonnements: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "overview_failed",
                "message": "Erreur lors de la récupération des données.",
            },
        )


@router.post("/admin/manual-activation", response_model=Dict[str, Any])
async def manual_activate_subscription(
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """
    Activation manuelle d'un abonnement.
    """
    logger.info(
        "Activation manuelle par %s pour user_id=%s",
        current_user.email,
        data.user_id,
    )

    try:
        subscription = upgrade_subscription(
            db=db,
            user_id=str(data.user_id),
            new_plan=data.plan,
            billing_cycle=data.billing_cycle,
            payment_id=data.payment_id,
            payment_method=data.payment_method,
            manual_activation=True,
            activated_by=str(current_user.id),
        )

        return {
            "success": True,
            "message": "Abonnement activé manuellement avec succès.",
            "subscription": {
                "id": str(subscription.id),
                "user_id": str(subscription.user_id),
                "tenant_id": to_str_uuid(subscription.tenant_id),
                "plan": subscription.plan_type,
                "plan_name": subscription.plan_name,
                "status": subscription.status,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining(),
                "price": float(subscription.price or 0),
                "billing_cycle": subscription.billing_cycle,
                "currency": getattr(subscription, "currency", "USD"),
            },
            "receipt": {
                "amount": float(subscription.price or 0),
                "currency": getattr(subscription, "currency", "USD"),
                "payment_method": data.payment_method,
                "reference": getattr(data, "reference", None) or f"MANUAL-{subscription.id}",
                "activated_by": current_user.email,
                "activated_at": utc_now_iso(),
            },
        }

    except ValueError as exc:
        logger.error("Erreur validation activation manuelle: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_request",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Erreur activation manuelle: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation manuelle. Veuillez réessayer.",
            },
        )


@router.post("/admin/extend-trial/{user_id}", response_model=Dict[str, Any])
async def extend_trial_period(
    user_id: str,
    extra_days: int = Query(7, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """
    Prolonge une période d'essai.
    """
    logger.info(
        "Prolongation essai demandée par %s pour %s (+%s jours)",
        current_user.email,
        user_id,
        extra_days,
    )

    try:
        user_uuid = UUID(user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_uuid",
                "message": "Format d'ID utilisateur invalide.",
            },
        )

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "user_not_found",
                "message": "Utilisateur non trouvé.",
            },
        )

    if not getattr(user, "subscription", None) or user.subscription.plan_type != "trial":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "not_in_trial",
                "message": "L'utilisateur n'est pas en période d'essai.",
            },
        )

    try:
        old_end = user.subscription.end_date
        if old_end is None:
            old_end = datetime.utcnow()

        new_end = old_end + timedelta(days=extra_days)

        user.subscription.end_date = new_end
        if hasattr(user.subscription, "trial_end_date"):
            user.subscription.trial_end_date = new_end

        config = user.subscription.config or {}
        config["trial_extended"] = {
            "extended_by": str(current_user.id),
            "extended_by_email": current_user.email,
            "extended_at": utc_now_iso(),
            "extra_days": extra_days,
            "old_end_date": old_end.isoformat() if old_end else None,
            "new_end_date": new_end.isoformat(),
        }
        user.subscription.config = config

        db.commit()
        db.refresh(user.subscription)

        return {
            "success": True,
            "message": f"Période d'essai prolongée de {extra_days} jours.",
            "user_id": str(user.id),
            "user_email": user.email,
            "old_end_date": old_end.isoformat() if old_end else None,
            "new_end_date": new_end.isoformat(),
            "days_remaining": user.subscription.days_remaining(),
            "extended_by": current_user.email,
            "extended_at": utc_now_iso(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error("Erreur prolongation essai: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "extension_failed",
                "message": "Erreur lors de la prolongation de l'essai.",
            },
        )


@router.get("/admin/tenant/{tenant_id}", response_model=Dict[str, Any])
async def get_tenant_subscriptions(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Dict[str, Any]:
    """
    Liste des abonnements d'un tenant.
    """
    logger.info(
        "Récupération abonnements tenant=%s par %s",
        tenant_id,
        current_user.email,
    )

    try:
        tenant_uuid = UUID(tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_uuid",
                "message": "Format d'ID tenant invalide.",
            },
        )

    try:
        users = db.query(User).filter(
            User.tenant_id == tenant_uuid,
            User.actif.is_(True),
        ).all()

        subscriptions: List[Dict[str, Any]] = []
        for user in users:
            sub = getattr(user, "subscription", None)
            if not sub:
                continue

            subscriptions.append({
                "user_id": str(user.id),
                "user_email": user.email,
                "user_role": user.role,
                "subscription": {
                    "id": str(sub.id),
                    "plan": sub.plan_type,
                    "plan_name": sub.plan_name,
                    "status": sub.status,
                    "is_active": sub.is_active(),
                    "start_date": sub.start_date.isoformat() if sub.start_date else None,
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining(),
                    "price": float(sub.price or 0),
                    "currency": getattr(sub, "currency", "USD"),
                },
            })

        return {
            "tenant_id": str(tenant_uuid),
            "total_users": len(users),
            "users_with_subscription": len(subscriptions),
            "subscriptions": subscriptions,
            "requested_by": current_user.email,
            "requested_at": utc_now_iso(),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erreur récupération abonnements tenant: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "fetch_failed",
                "message": "Erreur lors de la récupération des données.",
            },
        )


# =============================================================================
# ENDPOINTS TECHNIQUES
# =============================================================================

@router.get("/health", response_model=Dict[str, Any])
async def health_check() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "subscriptions-api",
        "timestamp": utc_now_iso(),
        "plans_available": list(PLAN_CONFIG.keys()),
    }