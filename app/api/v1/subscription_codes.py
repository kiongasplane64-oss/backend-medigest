# app/api/v1/subscription_codes.py
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.user import User
from app.models.tenant import Tenant
from app.models.subscription import Subscription, SubscriptionPlan, SubscriptionStatus, BillingPeriod, PaymentMethod, SubscriptionPayment, PaymentStatus
from app.schemas.subscription import (
    SubscriptionCodeCreate, 
    SubscriptionCodeResponse,
    ActivateSubscriptionCode,
    ValidateCodeResponse
)
from app.services.subscription_service import PLAN_CONFIG

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription-codes", tags=["Subscription Codes"])

# Configuration des plans (basée sur create_default_plans())
PLAN_CONFIG = {
    "starter": {
        "name": "Starter",
        "price_monthly": 49.99,
        "price_annual": 479.99,
        "max_users": 3,
        "max_products": 500,
        "max_storage_mb": 1024,
    },
    "professional": {
        "name": "Professional",
        "price_monthly": 89.99,
        "price_annual": 899.99,
        "max_users": 10,
        "max_products": None,
        "max_storage_mb": 5120,
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 149.99,
        "price_annual": 1499.99,
        "max_users": None,
        "max_products": None,
        "max_storage_mb": 10240,
    },
    "essai": {
        "name": "Essai Gratuit",
        "price_monthly": 0.00,
        "price_annual": 0.00,
        "max_users": 2,
        "max_products": 100,
        "max_storage_mb": 512,
    }
}


def generate_unique_code(length: int = 8) -> str:
    """Génère un code unique alphanumérique"""
    chars = string.ascii_uppercase + string.digits
    # Exclure les caractères ambigus O, 0, I, 1
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    return ''.join(random.choices(chars, k=length))


def format_code_with_dashes(code: str) -> str:
    """Formate le code avec des tirets tous les 4 caractères"""
    clean = code.replace('-', '').replace(' ', '').upper()
    if len(clean) >= 8:
        return f"{clean[:4]}-{clean[4:8]}"
    return code


def validate_code_logic(db: Session, code: str) -> Dict[str, Any]:
    """
    Logique de validation du code (utilisée par GET et POST)
    """
    # Nettoyer le code
    clean_code = code.strip().upper().replace('-', '').replace(' ', '')
    
    # Essayer différentes formats pour la recherche
    search_variations = [
        code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code,
    ]
    
    code_obj = None
    for search_code in search_variations:
        code_obj = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code
        ).first()
        if code_obj:
            break
    
    if not code_obj:
        return {
            "valid": False,
            "message": "Code invalide."
        }
    
    if not code_obj.is_valid():
        status_text = "expiré" if code_obj.valid_until and datetime.utcnow() > code_obj.valid_until else "déjà utilisé"
        return {
            "valid": False,
            "message": f"Code {status_text}.",
            "status": code_obj.status,
            "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None
        }
    
    return {
        "valid": True,
        "message": "Code valide.",
        "plan": {
            "type": code_obj.plan_type,
            "name": code_obj.plan_name,
            "duration_days": code_obj.duration_days
        },
        "price": float(code_obj.price) if code_obj.price else 0,
        "currency": code_obj.currency or "EUR",
        "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None,
        "code": code_obj.code,
        "tenant_id": str(code_obj.tenant_id) if code_obj.tenant_id else None,
        "user_id": str(code_obj.user_id) if code_obj.user_id else None
    }


def activate_tenant_subscription_with_code(
    db: Session,
    tenant: Tenant,
    code: SubscriptionCode,
    activated_by: User
) -> Subscription:
    """
    Active un abonnement pour un tenant avec un code.
    """
    # Définir la période de facturation
    billing_period = BillingPeriod.ANNUEL if code.duration_days >= 365 else BillingPeriod.MENSUEL
    
    # Obtenir les prix selon le plan
    plan_config = PLAN_CONFIG.get(code.plan_type, {})
    monthly_price = Decimal(str(plan_config.get("price_monthly", 0)))
    annual_price = Decimal(str(plan_config.get("price_annual", 0)))
    
    # Vérifier si le tenant a déjà un abonnement
    existing_sub = db.query(Subscription).filter(
        Subscription.tenant_id == tenant.id,
        Subscription.user_id == tenant.owner_id
    ).first()
    
    if existing_sub:
        # Mettre à jour l'abonnement existant
        existing_sub.plan = SubscriptionPlan(code.plan_type)
        existing_sub.plan_name = plan_config.get("name", code.plan_type)
        existing_sub.billing_period = billing_period
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.monthly_price = monthly_price
        existing_sub.annual_price = annual_price
        existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
        existing_sub.start_date = datetime.utcnow()
        existing_sub.end_date = datetime.utcnow() + timedelta(days=code.duration_days)
        existing_sub.next_billing_date = existing_sub.end_date
        existing_sub.auto_renew = False  # Les codes ne se renouvellent pas automatiquement
        existing_sub.updated_at = datetime.utcnow()
        subscription = existing_sub
    else:
        # Créer un nouvel abonnement
        subscription = Subscription(
            tenant_id=tenant.id,
            user_id=tenant.owner_id,
            plan=SubscriptionPlan(code.plan_type),
            plan_name=plan_config.get("name", code.plan_type),
            billing_period=billing_period,
            status=SubscriptionStatus.ACTIVE,
            monthly_price=monthly_price,
            annual_price=annual_price,
            current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=code.duration_days),
            next_billing_date=datetime.utcnow() + timedelta(days=code.duration_days),
            max_users=plan_config.get("max_users", 3),
            max_products=plan_config.get("max_products"),
            max_storage_mb=plan_config.get("max_storage_mb", 1024),
            auto_renew=False,
            created_by=activated_by.id,
            notes=f"Activé avec le code: {code.code}"
        )
        db.add(subscription)
    
    db.flush()
    
    # Créer un enregistrement de paiement
    payment = SubscriptionPayment(
        subscription_id=subscription.id,
        amount=Decimal(str(code.price)) if code.price else subscription.current_price,
        amount_paid=Decimal(str(code.price)) if code.price else subscription.current_price,
        status=PaymentStatus.COMPLETED,
        payment_method=PaymentMethod.CASH,
        payment_reference=f"CODE-{code.code}",
        period_start=subscription.start_date,
        period_end=subscription.end_date,
        description=f"Activation avec code - {subscription.plan_name}",
        notes=f"Code d'abonnement: {code.code}",
        paid_at=datetime.utcnow()
    )
    db.add(payment)
    
    db.flush()
    
    return subscription


def activate_user_subscription_with_code(
    db: Session,
    user: User,
    code: SubscriptionCode,
    activated_by: User
) -> Subscription:
    """
    Active un abonnement pour un utilisateur avec un code.
    """
    # Définir la période de facturation
    billing_period = BillingPeriod.ANNUEL if code.duration_days >= 365 else BillingPeriod.MENSUEL
    
    # Obtenir les prix selon le plan
    plan_config = PLAN_CONFIG.get(code.plan_type, {})
    monthly_price = Decimal(str(plan_config.get("price_monthly", 0)))
    annual_price = Decimal(str(plan_config.get("price_annual", 0)))
    
    # Vérifier si l'utilisateur a déjà un abonnement
    existing_sub = user.subscription
    
    if existing_sub:
        # Mettre à jour l'abonnement existant
        existing_sub.plan = SubscriptionPlan(code.plan_type)
        existing_sub.plan_name = plan_config.get("name", code.plan_type)
        existing_sub.billing_period = billing_period
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.monthly_price = monthly_price
        existing_sub.annual_price = annual_price
        existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
        existing_sub.start_date = datetime.utcnow()
        existing_sub.end_date = datetime.utcnow() + timedelta(days=code.duration_days)
        existing_sub.next_billing_date = existing_sub.end_date
        existing_sub.auto_renew = False
        existing_sub.updated_at = datetime.utcnow()
        subscription = existing_sub
    else:
        # Créer un nouvel abonnement
        subscription = Subscription(
            tenant_id=user.tenant_id,
            user_id=user.id,
            plan=SubscriptionPlan(code.plan_type),
            plan_name=plan_config.get("name", code.plan_type),
            billing_period=billing_period,
            status=SubscriptionStatus.ACTIVE,
            monthly_price=monthly_price,
            annual_price=annual_price,
            current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=code.duration_days),
            next_billing_date=datetime.utcnow() + timedelta(days=code.duration_days),
            max_users=1,
            max_products=plan_config.get("max_products"),
            max_storage_mb=plan_config.get("max_storage_mb", 1024),
            auto_renew=False,
            created_by=activated_by.id,
            notes=f"Activé avec le code: {code.code}"
        )
        db.add(subscription)
    
    db.flush()
    
    # Créer un enregistrement de paiement
    payment = SubscriptionPayment(
        subscription_id=subscription.id,
        amount=Decimal(str(code.price)) if code.price else subscription.current_price,
        amount_paid=Decimal(str(code.price)) if code.price else subscription.current_price,
        status=PaymentStatus.COMPLETED,
        payment_method=PaymentMethod.CASH,
        payment_reference=f"CODE-{code.code}",
        period_start=subscription.start_date,
        period_end=subscription.end_date,
        description=f"Activation avec code - {subscription.plan_name}",
        notes=f"Code d'abonnement: {code.code}",
        paid_at=datetime.utcnow()
    )
    db.add(payment)
    
    db.flush()
    
    return subscription


# =============================================================================
# ENDPOINTS SUPER ADMIN - GESTION DES CODES
# =============================================================================

@router.post("/admin/generate", response_model=Dict[str, Any])
async def generate_subscription_code(
    data: SubscriptionCodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Génère un code d'abonnement pour paiement cash.
    Accessible uniquement aux super admins.
    
    Peut être associé à :
    - Un tenant spécifique (pharmacie)
    - Un utilisateur spécifique
    - Aucun (code générique)
    """
    logger.info(f"Génération de code abonnement par {current_user.email} pour plan {data.plan_type}")
    
    # Vérifier que le plan existe
    if data.plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_plan",
                "message": f"Le plan {data.plan_type} n'existe pas."
            }
        )
    
    # Validation des IDs si fournis
    assigned_tenant = None
    assigned_user = None
    
    if data.tenant_id:
        assigned_tenant = db.query(Tenant).filter(Tenant.id == data.tenant_id).first()
        if not assigned_tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tenant_not_found",
                    "message": "La pharmacie spécifiée n'existe pas."
                }
            )
    
    if data.user_id:
        assigned_user = db.query(User).filter(User.id == data.user_id).first()
        if not assigned_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "user_not_found",
                    "message": "L'utilisateur spécifié n'existe pas."
                }
            )
        
        # Vérifier que l'utilisateur est bien associé au tenant si les deux sont fournis
        if data.tenant_id and assigned_user.tenant_id != data.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "user_tenant_mismatch",
                    "message": "L'utilisateur n'appartient pas à la pharmacie spécifiée."
                }
            )
    
    plan_config = PLAN_CONFIG[data.plan_type]
    
    # Générer un code unique
    attempts = 0
    max_attempts = 10
    generated_code = None
    
    while attempts < max_attempts:
        raw_code = generate_unique_code(data.code_length or 8)
        formatted_code = format_code_with_dashes(raw_code)
        
        existing = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == formatted_code
        ).first()
        
        if not existing:
            generated_code = formatted_code
            break
        attempts += 1
    
    if not generated_code:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "code_generation_failed",
                "message": "Impossible de générer un code unique après plusieurs tentatives."
            }
        )
    
    # Calculer la durée en jours
    duration_days = data.duration_days
    if not duration_days:
        duration_days = 365 if data.billing_cycle == "yearly" else 30
    
    # Calculer le prix
    price = data.price
    if not price:
        price_key = f"price_{data.billing_cycle or 'monthly'}"
        price = plan_config.get(price_key, 0)
    
    # Créer le code
    code = SubscriptionCode(
        code=generated_code,
        plan_type=data.plan_type,
        plan_name=plan_config.get("name", data.plan_type.title()),
        duration_days=duration_days,
        price=price,
        currency=data.currency or "EUR",
        valid_from=data.valid_from or datetime.utcnow(),
        valid_until=data.valid_until or (datetime.utcnow() + timedelta(days=data.expiry_days or 90)),
        notes=data.notes,
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
        tenant_id=data.tenant_id,
        user_id=data.user_id
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    logger.info(f"Code généré avec succès: {generated_code} pour tenant: {data.tenant_id}, user: {data.user_id}")
    
    return {
        "success": True,
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": float(code.price),
        "currency": code.currency,
        "duration_days": code.duration_days,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "created_at": code.created_at.isoformat() if code.created_at else None,
        "status": code.status.value if hasattr(code.status, 'value') else code.status,
        "tenant_id": str(code.tenant_id) if code.tenant_id else None,
        "user_id": str(code.user_id) if code.user_id else None,
        "tenant_name": assigned_tenant.nom_pharmacie if assigned_tenant else None,
        "user_email": assigned_user.email if assigned_user else None
    }


@router.get("/admin/list", response_model=Dict[str, Any])
async def list_subscription_codes(
    status: Optional[str] = Query(None),
    tenant_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    user_id: Optional[UUID] = Query(None, description="Filtrer par utilisateur"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Liste tous les codes d'abonnement générés.
    Possibilité de filtrer par tenant ou utilisateur.
    """
    query = db.query(SubscriptionCode)
    
    if status:
        try:
            status_enum = SubscriptionCodeStatus(status)
            query = query.filter(SubscriptionCode.status == status_enum)
        except ValueError:
            pass
    
    if tenant_id:
        query = query.filter(SubscriptionCode.tenant_id == tenant_id)
    
    if user_id:
        query = query.filter(SubscriptionCode.user_id == user_id)
    
    total = query.count()
    
    codes = query.order_by(SubscriptionCode.created_at.desc())\
                 .offset((page - 1) * limit)\
                 .limit(limit)\
                 .all()
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "codes": [
            {
                "id": str(code.id),
                "code": code.code,
                "plan_type": code.plan_type,
                "plan_name": code.plan_name,
                "price": float(code.price) if code.price else 0,
                "currency": code.currency or "EUR",
                "duration_days": code.duration_days,
                "status": code.status.value if hasattr(code.status, 'value') else code.status,
                "valid_until": code.valid_until.isoformat() if code.valid_until else None,
                "created_at": code.created_at.isoformat() if code.created_at else None,
                "activated_by": code.activated_by_user.email if code.activated_by_user else None,
                "activated_at": code.activated_at.isoformat() if code.activated_at else None,
                "created_by": code.created_by_user.email if code.created_by_user else None,
                "tenant_id": str(code.tenant_id) if code.tenant_id else None,
                "user_id": str(code.user_id) if code.user_id else None,
                "tenant_name": code.tenant.nom_pharmacie if code.tenant else None,
                "user_email": code.assigned_user.email if code.assigned_user else None
            }
            for code in codes
        ]
    }


@router.get("/admin/{code_id}", response_model=Dict[str, Any])
async def get_subscription_code_details(
    code_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Détails d'un code spécifique.
    """
    code = db.query(SubscriptionCode).filter(SubscriptionCode.id == code_id).first()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "code_not_found",
                "message": "Code d'abonnement non trouvé."
            }
        )
    
    return {
        "id": str(code.id),
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": float(code.price) if code.price else 0,
        "currency": code.currency or "EUR",
        "duration_days": code.duration_days,
        "status": code.status.value if hasattr(code.status, 'value') else code.status,
        "valid_from": code.valid_from.isoformat() if code.valid_from else None,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "created_at": code.created_at.isoformat() if code.created_at else None,
        "created_by": code.created_by_user.email if code.created_by_user else None,
        "activated_by": code.activated_by_user.email if code.activated_by_user else None,
        "activated_at": code.activated_at.isoformat() if code.activated_at else None,
        "notes": code.notes,
        "is_valid": code.is_valid(),
        "days_remaining": code.days_remaining(),
        "tenant_id": str(code.tenant_id) if code.tenant_id else None,
        "user_id": str(code.user_id) if code.user_id else None,
        "tenant_name": code.tenant.nom_pharmacie if code.tenant else None,
        "user_email": code.assigned_user.email if code.assigned_user else None
    }


@router.post("/admin/manual-activate/{target_id}")
async def manual_activate(
    target_id: UUID,
    activation_type: str = Query(..., pattern="^(tenant|user)$", description="Type: tenant ou user"),
    plan_type: str = Query(..., description="Type de plan"),
    duration_days: int = Query(30, ge=1, le=3650, description="Durée en jours"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Activation manuelle d'un tenant ou d'un utilisateur (paiement cash sans code).
    Le super admin peut activer directement un compte.
    """
    logger.info(f"Activation manuelle de {activation_type} {target_id} par {current_user.email}")
    
    if activation_type == "tenant":
        tenant = db.query(Tenant).filter(Tenant.id == target_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tenant_not_found",
                    "message": "Tenant non trouvé."
                }
            )
        
        if not tenant.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "no_owner",
                    "message": "Le tenant n'a pas de propriétaire."
                }
            )
        
        # Vérifier que le plan existe
        if plan_type not in PLAN_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_plan",
                    "message": f"Le plan {plan_type} n'existe pas."
                }
            )
        
        plan_config = PLAN_CONFIG[plan_type]
        
        # Définir la période de facturation
        billing_period = BillingPeriod.ANNUEL if duration_days >= 365 else BillingPeriod.MENSUEL
        
        monthly_price = Decimal(str(plan_config.get("price_monthly", 0)))
        annual_price = Decimal(str(plan_config.get("price_annual", 0)))
        
        # Vérifier si le tenant a déjà un abonnement
        existing_sub = db.query(Subscription).filter(
            Subscription.tenant_id == tenant.id,
            Subscription.user_id == tenant.owner_id
        ).first()
        
        if existing_sub:
            existing_sub.plan = SubscriptionPlan(plan_type)
            existing_sub.plan_name = plan_config.get("name", plan_type)
            existing_sub.billing_period = billing_period
            existing_sub.status = SubscriptionStatus.ACTIVE
            existing_sub.monthly_price = monthly_price
            existing_sub.annual_price = annual_price
            existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
            existing_sub.start_date = datetime.utcnow()
            existing_sub.end_date = datetime.utcnow() + timedelta(days=duration_days)
            existing_sub.next_billing_date = existing_sub.end_date
            existing_sub.auto_renew = False
            existing_sub.updated_at = datetime.utcnow()
            subscription = existing_sub
        else:
            subscription = Subscription(
                tenant_id=tenant.id,
                user_id=tenant.owner_id,
                plan=SubscriptionPlan(plan_type),
                plan_name=plan_config.get("name", plan_type),
                billing_period=billing_period,
                status=SubscriptionStatus.ACTIVE,
                monthly_price=monthly_price,
                annual_price=annual_price,
                current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
                start_date=datetime.utcnow(),
                end_date=datetime.utcnow() + timedelta(days=duration_days),
                next_billing_date=datetime.utcnow() + timedelta(days=duration_days),
                max_users=plan_config.get("max_users", 3),
                max_products=plan_config.get("max_products"),
                max_storage_mb=plan_config.get("max_storage_mb", 1024),
                auto_renew=False,
                created_by=current_user.id,
                notes=f"Activation manuelle par {current_user.email}"
            )
            db.add(subscription)
        
        db.flush()
        
        # Créer un enregistrement de paiement
        payment = SubscriptionPayment(
            subscription_id=subscription.id,
            amount=subscription.current_price,
            amount_paid=subscription.current_price,
            status=PaymentStatus.COMPLETED,
            payment_method=PaymentMethod.CASH,
            payment_reference=f"MANUAL-{tenant.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            period_start=subscription.start_date,
            period_end=subscription.end_date,
            description=f"Activation manuelle - {subscription.plan_name}",
            notes=f"Activation par super admin: {current_user.email}",
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Tenant {tenant.nom_pharmacie} activé manuellement pour {duration_days} jours.",
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.nom_pharmacie,
                "owner_email": tenant.owner.email if tenant.owner else None
            },
            "subscription": {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining()
            },
            "activated_by": current_user.email,
            "activated_at": datetime.utcnow().isoformat()
        }
    
    else:  # activation_type == "user"
        user = db.query(User).filter(User.id == target_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "user_not_found",
                    "message": "Utilisateur non trouvé."
                }
            )
        
        if not user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "no_tenant",
                    "message": "L'utilisateur n'est pas rattaché à une pharmacie."
                }
            )
        
        if plan_type not in PLAN_CONFIG:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "invalid_plan",
                    "message": f"Le plan {plan_type} n'existe pas."
                }
            )
        
        plan_config = PLAN_CONFIG[plan_type]
        billing_period = BillingPeriod.ANNUEL if duration_days >= 365 else BillingPeriod.MENSUEL
        
        monthly_price = Decimal(str(plan_config.get("price_monthly", 0)))
        annual_price = Decimal(str(plan_config.get("price_annual", 0)))
        
        existing_sub = user.subscription
        
        if existing_sub:
            existing_sub.plan = SubscriptionPlan(plan_type)
            existing_sub.plan_name = plan_config.get("name", plan_type)
            existing_sub.billing_period = billing_period
            existing_sub.status = SubscriptionStatus.ACTIVE
            existing_sub.monthly_price = monthly_price
            existing_sub.annual_price = annual_price
            existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
            existing_sub.start_date = datetime.utcnow()
            existing_sub.end_date = datetime.utcnow() + timedelta(days=duration_days)
            existing_sub.next_billing_date = existing_sub.end_date
            existing_sub.auto_renew = False
            existing_sub.updated_at = datetime.utcnow()
            subscription = existing_sub
        else:
            subscription = Subscription(
                tenant_id=user.tenant_id,
                user_id=user.id,
                plan=SubscriptionPlan(plan_type),
                plan_name=plan_config.get("name", plan_type),
                billing_period=billing_period,
                status=SubscriptionStatus.ACTIVE,
                monthly_price=monthly_price,
                annual_price=annual_price,
                current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
                start_date=datetime.utcnow(),
                end_date=datetime.utcnow() + timedelta(days=duration_days),
                next_billing_date=datetime.utcnow() + timedelta(days=duration_days),
                max_users=1,
                max_products=plan_config.get("max_products"),
                max_storage_mb=plan_config.get("max_storage_mb", 1024),
                auto_renew=False,
                created_by=current_user.id,
                notes=f"Activation manuelle par {current_user.email}"
            )
            db.add(subscription)
        
        db.flush()
        
        payment = SubscriptionPayment(
            subscription_id=subscription.id,
            amount=subscription.current_price,
            amount_paid=subscription.current_price,
            status=PaymentStatus.COMPLETED,
            payment_method=PaymentMethod.CASH,
            payment_reference=f"MANUAL-USER-{user.id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            period_start=subscription.start_date,
            period_end=subscription.end_date,
            description=f"Activation manuelle - {subscription.plan_name}",
            notes=f"Activation par super admin: {current_user.email}",
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Utilisateur {user.email} activé manuellement pour {duration_days} jours.",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": user.nom_complet,
                "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                "tenant_name": user.tenant.nom_pharmacie if user.tenant else None
            },
            "subscription": {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining()
            },
            "activated_by": current_user.email,
            "activated_at": datetime.utcnow().isoformat()
        }


# =============================================================================
# ENDPOINTS UTILISATEUR - ACTIVATION AVEC CODE
# =============================================================================

@router.post("/activate", response_model=Dict[str, Any])
async def activate_with_code(
    data: ActivateSubscriptionCode,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Active un abonnement avec un code (paiement cash).
    Vérifie que le code est valide et non utilisé.
    Si le code est associé à un tenant, vérifie que l'utilisateur appartient à ce tenant.
    """
    logger.info(f"Tentative d'activation avec code par {current_user.email}")
    
    # Nettoyer le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    
    # Chercher le code
    search_variations = [
        data.code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code
    ]
    
    code = None
    for search_code in search_variations:
        code = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code,
            SubscriptionCode.status == SubscriptionCodeStatus.PENDING
        ).first()
        if code:
            break
    
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "invalid_code",
                "message": "Code d'abonnement invalide ou déjà utilisé."
            }
        )
    
    # Vérifier que le tenant correspond (si le code est associé à un tenant)
    if code.tenant_id and current_user.tenant_id != code.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_tenant",
                "message": "Ce code est réservé à une autre pharmacie."
            }
        )
    
    # Vérifier que l'utilisateur correspond (si le code est associé à un utilisateur)
    if code.user_id and current_user.id != code.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "invalid_user",
                "message": "Ce code est réservé à un autre utilisateur."
            }
        )
    
    # Vérifier validité du code
    if not code.is_valid():
        status_text = "expiré" if code.valid_until and datetime.utcnow() > code.valid_until else "non disponible"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_expired",
                "message": f"Ce code est {status_text}.",
                "valid_until": code.valid_until.isoformat() if code.valid_until else None
            }
        )
    
    try:
        # Vérifier si c'est un code pour tenant (administrateur) ou pour utilisateur
        if code.tenant_id and current_user.role == "admin":
            # Activer pour le tenant
            subscription = activate_tenant_subscription_with_code(
                db=db,
                tenant=current_user.tenant,
                code=code,
                activated_by=current_user
            )
        else:
            # Activer pour l'utilisateur
            subscription = activate_user_subscription_with_code(
                db=db,
                user=current_user,
                code=code,
                activated_by=current_user
            )
        
        # Marquer le code comme utilisé
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "success": True,
            "message": "Abonnement activé avec succès !",
            "subscription": {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining()
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type,
                "duration_days": code.duration_days
            }
        }
        
    except Exception as exc:
        db.rollback()
        logger.error(f"Erreur activation code: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation. Veuillez contacter le support."
            }
        )


@router.post("/activate-tenant/{tenant_id}", response_model=Dict[str, Any])
async def activate_tenant_with_code(
    tenant_id: UUID,
    data: ActivateSubscriptionCode,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Active un abonnement pour un tenant spécifique avec un code.
    Réservé aux super admins.
    """
    logger.info(f"Activation de tenant {tenant_id} avec code par {current_user.email}")
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "tenant_not_found",
                "message": "Tenant non trouvé."
            }
        )
    
    if not tenant.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "no_owner",
                "message": "Le tenant n'a pas de propriétaire."
            }
        )
    
    # Nettoyer le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    
    search_variations = [
        data.code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code
    ]
    
    code = None
    for search_code in search_variations:
        code = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code,
            SubscriptionCode.status == SubscriptionCodeStatus.PENDING
        ).first()
        if code:
            break
    
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "invalid_code",
                "message": "Code d'abonnement invalide ou déjà utilisé."
            }
        )
    
    if not code.is_valid():
        status_text = "expiré" if code.valid_until and datetime.utcnow() > code.valid_until else "non disponible"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_expired",
                "message": f"Ce code est {status_text}."
            }
        )
    
    try:
        subscription = activate_tenant_subscription_with_code(
            db=db,
            tenant=tenant,
            code=code,
            activated_by=current_user
        )
        
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Abonnement activé pour le tenant {tenant.nom_pharmacie}",
            "tenant": {
                "id": str(tenant.id),
                "name": tenant.nom_pharmacie,
                "owner_email": tenant.owner.email if tenant.owner else None
            },
            "subscription": {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining()
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type
            }
        }
        
    except Exception as exc:
        db.rollback()
        logger.error(f"Erreur activation tenant: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation."
            }
        )


# =============================================================================
# ENDPOINTS DE VALIDATION (GET ET POST)
# =============================================================================

@router.get("/validate", response_model=Dict[str, Any])
@router.post("/validate", response_model=Dict[str, Any])
async def validate_code(
    code: str = Query(None, description="Code à valider"),
    db: Session = Depends(get_db),
    data: Optional[ActivateSubscriptionCode] = None,
) -> Any:
    """
    Valide un code sans l'activer.
    Accepte GET (avec query param) ou POST (avec body).
    """
    # Déterminer le code à valider
    code_to_validate = None
    if data:
        code_to_validate = data.code
    elif code:
        code_to_validate = code
    
    if not code_to_validate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_code",
                "message": "Code d'abonnement requis."
            }
        )
    
    logger.info(f"Validation de code: {code_to_validate}")
    result = validate_code_logic(db, code_to_validate)
    
    if not result["valid"]:
        logger.warning(f"Code invalide: {code_to_validate} - {result['message']}")
    
    return result


# =============================================================================
# ENDPOINT DE DÉBOGAGE
# =============================================================================

@router.post("/debug/generate-test", include_in_schema=False)
async def debug_generate_test_code(
    plan_type: str = "professional",
    tenant_id: Optional[UUID] = None,
    user_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Endpoint de débogage pour générer un code de test.
    (Non documenté, accessible seulement en développement)
    """
    if plan_type not in PLAN_CONFIG:
        plan_type = "professional"
    
    raw_code = generate_unique_code(8)
    formatted_code = format_code_with_dashes(raw_code)
    
    plan_config = PLAN_CONFIG[plan_type]
    
    code = SubscriptionCode(
        code=formatted_code,
        plan_type=plan_type,
        plan_name=plan_config.get("name", plan_type),
        duration_days=30,
        price=plan_config.get("price_monthly", 0),
        currency="EUR",
        valid_from=datetime.utcnow(),
        valid_until=datetime.utcnow() + timedelta(days=90),
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
        notes="Code de test généré automatiquement",
        tenant_id=tenant_id,
        user_id=user_id
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    return {
        "success": True,
        "message": "Code d'abonnement généré",
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "tenant_id": str(code.tenant_id) if code.tenant_id else None,
        "user_id": str(code.user_id) if code.user_id else None
    }