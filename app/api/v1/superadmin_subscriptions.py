# app/api/v1/superadmin_subscriptions.py
"""
Endpoints de gestion des abonnements pour les super administrateurs.
Permet l'activation manuelle, la prolongation d'essais et la vue d'ensemble.
Gère les abonnements des tenants ET des utilisateurs.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_
from typing import Optional, Dict, Any, List
from uuid import UUID
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from app.api.deps import get_db, verify_super_admin
from app.models.user import User
from app.models.tenant import Tenant
from app.models.subscription import (
    Subscription, SubscriptionPlan, SubscriptionStatus,
    BillingPeriod, PaymentMethod, SubscriptionPayment, PaymentStatus
)
from app.schemas.subscription import ManualActivationSchema, SubscriptionFilterSchema

# Configuration du logger
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/super-admin/subscriptions", tags=["Super Admin - Subscriptions"])

# Configuration des plans (basée sur create_default_plans())
PLAN_CONFIG = {
    SubscriptionPlan.STARTER: {
        "name": "Starter",
        "price_monthly": 49.99,
        "price_annual": 479.99,
        "max_users": 3,
        "max_products": 500,
        "max_storage_mb": 1024,
    },
    SubscriptionPlan.PROFESSIONAL: {
        "name": "Professional",
        "price_monthly": 89.99,
        "price_annual": 899.99,
        "max_users": 10,
        "max_products": None,
        "max_storage_mb": 5120,
    },
    SubscriptionPlan.ENTERPRISE: {
        "name": "Enterprise",
        "price_monthly": 149.99,
        "price_annual": 1499.99,
        "max_users": None,
        "max_products": None,
        "max_storage_mb": 10240,
    },
    SubscriptionPlan.ESSAI: {
        "name": "Essai Gratuit",
        "price_monthly": 0.00,
        "price_annual": 0.00,
        "max_users": 2,
        "max_products": 100,
        "max_storage_mb": 512,
    }
}


# =============================================================================
# VUE D'ENSEMBLE
# =============================================================================

@router.get("/overview", response_model=Dict[str, Any])
async def get_subscriptions_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    tenant_id: Optional[str] = Query(None, description="Filtrer par ID de tenant (optionnel)"),
    include_users: bool = Query(False, description="Inclure les abonnements utilisateurs")
) -> Dict[str, Any]:
    """
    Vue d'ensemble des abonnements pour le super admin.
    Gère à la fois les abonnements des tenants et des utilisateurs.
    
    Args:
        tenant_id: ID du tenant pour filtrer (optionnel)
        include_users: Si True, inclut les abonnements utilisateurs
        
    Returns:
        Statistiques globales et liste détaillée des abonnements
    """
    logger.info(f"Demande de vue d'ensemble des abonnements par {current_user.email}")
    
    try:
        # Construire la requête de base pour les tenants
        tenant_query = db.query(Tenant)
        if tenant_id:
            tenant_query = tenant_query.filter(Tenant.id == tenant_id)
        
        tenants = tenant_query.all()
        
        # Statistiques tenants
        tenant_subscriptions = []
        total_tenants = len(tenants)
        trial_tenants = 0
        active_paid_tenants = 0
        plans_distribution = {plan.value: 0 for plan in SubscriptionPlan}
        
        for tenant in tenants:
            sub = db.query(Subscription).filter(
                Subscription.tenant_id == tenant.id,
                Subscription.user_id == tenant.owner_id
            ).first()
            
            if sub:
                sub_data = {
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.nom_pharmacie,
                    "tenant_code": tenant.tenant_code,
                    "owner_email": tenant.owner.email if tenant.owner else None,
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "is_active": sub.is_active(),
                    "is_trial": sub.is_trial(),
                    "start_date": sub.start_date.isoformat() if sub.start_date else None,
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "trial_end_date": sub.trial_end_date.isoformat() if sub.trial_end_date else None,
                    "days_remaining": sub.days_remaining(),
                    "trial_days_remaining": sub.trial_days_remaining(),
                    "current_price": float(sub.current_price),
                    "auto_renew": sub.auto_renew
                }
                tenant_subscriptions.append(sub_data)
                
                # Mise à jour des statistiques
                if sub.status == SubscriptionStatus.TRIAL:
                    trial_tenants += 1
                elif sub.status == SubscriptionStatus.ACTIVE:
                    active_paid_tenants += 1
                
                if sub.plan.value in plans_distribution:
                    plans_distribution[sub.plan.value] += 1
        
        # Abonnements utilisateurs (optionnel)
        user_subscriptions = []
        if include_users:
            users = db.query(User).filter(User.tenant_id.isnot(None))
            if tenant_id:
                users = users.filter(User.tenant_id == tenant_id)
            
            for user in users.all():
                sub = user.subscription
                if sub:
                    user_subscriptions.append({
                        "user_id": str(user.id),
                        "user_email": user.email,
                        "user_name": user.nom_complet,
                        "tenant_id": str(user.tenant_id),
                        "tenant_name": user.tenant.nom_pharmacie if user.tenant else None,
                        "subscription_id": str(sub.id),
                        "plan": sub.plan.value,
                        "plan_name": sub.plan_name,
                        "status": sub.status.value,
                        "is_active": sub.is_active(),
                        "is_trial": sub.is_trial(),
                        "end_date": sub.end_date.isoformat() if sub.end_date else None,
                        "days_remaining": sub.days_remaining()
                    })
        
        # Calcul des revenus projetés
        monthly_revenue = sum([
            PLAN_CONFIG.get(sub["plan"], {}).get("price_monthly", 0) 
            for sub in tenant_subscriptions 
            if sub.get("is_active", False) and sub.get("plan") != SubscriptionPlan.ESSAI.value
        ])
        
        result = {
            "timestamp": datetime.utcnow().isoformat(),
            "requested_by": current_user.email,
            "filter": {"tenant_id": tenant_id} if tenant_id else None,
            "tenants": {
                "total": total_tenants,
                "trial": trial_tenants,
                "active_paid": active_paid_tenants,
                "conversion_rate": round(active_paid_tenants / max(trial_tenants + active_paid_tenants, 1) * 100, 2),
                "subscriptions": tenant_subscriptions
            },
            "plans_distribution": plans_distribution,
            "revenue": {
                "monthly": monthly_revenue,
                "yearly": monthly_revenue * 12,
                "average_per_tenant": round(monthly_revenue / max(active_paid_tenants, 1), 2)
            }
        }
        
        if include_users:
            result["users"] = {
                "total": len(user_subscriptions),
                "subscriptions": user_subscriptions
            }
        
        logger.info(f"Vue d'ensemble récupérée: {total_tenants} tenants, {len(user_subscriptions)} utilisateurs")
        return result
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération de la vue d'ensemble: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "overview_failed",
                "message": "Erreur lors de la récupération des données d'abonnement"
            }
        )


# =============================================================================
# ACTIVATION MANUELLE (PAIEMENT CASH) - POUR TENANT
# =============================================================================

@router.post("/manual-activation/tenant", response_model=Dict[str, Any])
async def manual_activate_tenant_subscription(
    tenant_id: UUID,
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Activation manuelle d'un abonnement tenant (paiement cash).
    Réservé aux super administrateurs pour les paiements hors ligne.
    
    Args:
        tenant_id: ID du tenant à activer
        data: Informations d'activation manuelle (plan, période, montant)
        
    Returns:
        Confirmation de l'activation avec détails de l'abonnement
    """
    logger.info(f"Activation manuelle d'abonnement tenant par {current_user.email} pour {tenant_id}")
    
    # Vérifier que le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "tenant_not_found", "message": "Tenant non trouvé"}
        )
    
    if not tenant.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "no_owner", "message": "Le tenant n'a pas de propriétaire"}
        )
    
    try:
        # Vérifier si un abonnement existe déjà
        existing_sub = db.query(Subscription).filter(
            Subscription.tenant_id == tenant_id,
            Subscription.user_id == tenant.owner_id
        ).first()
        
        # Définir la période de facturation
        billing_period = BillingPeriod.MENSUEL if data.billing_cycle == "monthly" else BillingPeriod.ANNUEL
        
        # Obtenir les prix selon le plan
        plan_prices = PLAN_CONFIG.get(data.plan, {})
        monthly_price = Decimal(str(plan_prices.get("price_monthly", 0)))
        annual_price = Decimal(str(plan_prices.get("price_annual", 0)))
        
        if existing_sub:
            # Mettre à jour l'abonnement existant
            existing_sub.plan = data.plan
            existing_sub.plan_name = plan_prices.get("name", data.plan.value)
            existing_sub.billing_period = billing_period
            existing_sub.status = SubscriptionStatus.ACTIVE
            existing_sub.monthly_price = monthly_price
            existing_sub.annual_price = annual_price
            existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
            existing_sub.start_date = datetime.utcnow()
            
            # Calculer la date de fin
            if billing_period == BillingPeriod.MENSUEL:
                existing_sub.end_date = datetime.utcnow() + timedelta(days=30)
            else:
                existing_sub.end_date = datetime.utcnow() + timedelta(days=365)
            
            existing_sub.next_billing_date = existing_sub.end_date
            existing_sub.auto_renew = data.auto_renew if hasattr(data, 'auto_renew') else True
            existing_sub.updated_at = datetime.utcnow()
            
            subscription = existing_sub
        else:
            # Créer un nouvel abonnement
            subscription = Subscription(
                tenant_id=tenant_id,
                user_id=tenant.owner_id,
                plan=data.plan,
                plan_name=plan_prices.get("name", data.plan.value),
                billing_period=billing_period,
                status=SubscriptionStatus.ACTIVE,
                monthly_price=monthly_price,
                annual_price=annual_price,
                current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
                start_date=datetime.utcnow(),
                max_users=plan_prices.get("max_users", 3),
                max_products=plan_prices.get("max_products"),
                max_storage_mb=plan_prices.get("max_storage_mb", 1024),
                auto_renew=data.auto_renew if hasattr(data, 'auto_renew') else True,
                created_by=current_user.id
            )
            db.add(subscription)
        
        db.flush()
        
        # Créer un enregistrement de paiement
        payment = SubscriptionPayment(
            subscription_id=subscription.id,
            amount=Decimal(str(data.amount)) if data.amount else subscription.current_price,
            amount_paid=Decimal(str(data.amount)) if data.amount else subscription.current_price,
            status=PaymentStatus.COMPLETED,
            payment_method=data.payment_method,
            payment_reference=data.reference or f"MANUAL-{subscription.id}",
            period_start=subscription.start_date,
            period_end=subscription.end_date,
            description=f"Activation manuelle - {subscription.plan_name}",
            notes=data.notes,
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        db.commit()
        db.refresh(subscription)
        
        response = {
            "message": "Abonnement tenant activé manuellement avec succès",
            "success": True,
            "subscription": {
                "id": str(subscription.id),
                "tenant_id": str(tenant.id),
                "tenant_name": tenant.nom_pharmacie,
                "owner_email": tenant.owner.email if tenant.owner else None,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "status": subscription.status.value,
                "billing_period": subscription.billing_period.value,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining(),
                "current_price": float(subscription.current_price),
                "activated_by": current_user.email,
                "activated_at": datetime.utcnow().isoformat()
            },
            "payment": {
                "id": str(payment.id),
                "amount": float(payment.amount),
                "payment_method": payment.payment_method.value,
                "reference": payment.payment_reference
            }
        }
        
        logger.info(f"Activation manuelle réussie pour tenant {tenant.nom_pharmacie} (plan: {data.plan.value})")
        return response
        
    except ValueError as e:
        db.rollback()
        logger.error(f"Erreur de validation lors de l'activation manuelle: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_request", "message": str(e)}
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur inattendue lors de l'activation manuelle: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "activation_failed", "message": "Erreur lors de l'activation manuelle"}
        )


# =============================================================================
# ACTIVATION MANUELLE - POUR UTILISATEUR
# =============================================================================

@router.post("/manual-activation/user", response_model=Dict[str, Any])
async def manual_activate_user_subscription(
    user_id: UUID,
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Activation manuelle d'un abonnement utilisateur (paiement cash).
    Réservé aux super administrateurs pour les paiements hors ligne.
    
    Args:
        user_id: ID de l'utilisateur à activer
        data: Informations d'activation manuelle (plan, période, montant)
        
    Returns:
        Confirmation de l'activation avec détails de l'abonnement
    """
    logger.info(f"Activation manuelle d'abonnement utilisateur par {current_user.email} pour {user_id}")
    
    # Vérifier que l'utilisateur existe
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "user_not_found", "message": "Utilisateur non trouvé"}
        )
    
    if not user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "no_tenant", "message": "L'utilisateur n'est pas rattaché à un tenant"}
        )
    
    try:
        # Vérifier si un abonnement existe déjà
        existing_sub = db.query(Subscription).filter(
            Subscription.user_id == user_id,
            Subscription.tenant_id == user.tenant_id
        ).first()
        
        # Définir la période de facturation
        billing_period = BillingPeriod.MENSUEL if data.billing_cycle == "monthly" else BillingPeriod.ANNUEL
        
        # Obtenir les prix selon le plan
        plan_prices = PLAN_CONFIG.get(data.plan, {})
        monthly_price = Decimal(str(plan_prices.get("price_monthly", 0)))
        annual_price = Decimal(str(plan_prices.get("price_annual", 0)))
        
        if existing_sub:
            # Mettre à jour l'abonnement existant
            existing_sub.plan = data.plan
            existing_sub.plan_name = plan_prices.get("name", data.plan.value)
            existing_sub.billing_period = billing_period
            existing_sub.status = SubscriptionStatus.ACTIVE
            existing_sub.monthly_price = monthly_price
            existing_sub.annual_price = annual_price
            existing_sub.current_price = annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price
            existing_sub.start_date = datetime.utcnow()
            
            if billing_period == BillingPeriod.MENSUEL:
                existing_sub.end_date = datetime.utcnow() + timedelta(days=30)
            else:
                existing_sub.end_date = datetime.utcnow() + timedelta(days=365)
            
            existing_sub.next_billing_date = existing_sub.end_date
            existing_sub.auto_renew = data.auto_renew if hasattr(data, 'auto_renew') else True
            existing_sub.updated_at = datetime.utcnow()
            
            subscription = existing_sub
        else:
            # Créer un nouvel abonnement
            subscription = Subscription(
                tenant_id=user.tenant_id,
                user_id=user_id,
                plan=data.plan,
                plan_name=plan_prices.get("name", data.plan.value),
                billing_period=billing_period,
                status=SubscriptionStatus.ACTIVE,
                monthly_price=monthly_price,
                annual_price=annual_price,
                current_price=annual_price if billing_period == BillingPeriod.ANNUEL else monthly_price,
                start_date=datetime.utcnow(),
                max_users=1,
                max_products=plan_prices.get("max_products"),
                max_storage_mb=plan_prices.get("max_storage_mb", 1024),
                auto_renew=data.auto_renew if hasattr(data, 'auto_renew') else True,
                created_by=current_user.id
            )
            db.add(subscription)
        
        db.flush()
        
        # Créer un enregistrement de paiement
        payment = SubscriptionPayment(
            subscription_id=subscription.id,
            amount=Decimal(str(data.amount)) if data.amount else subscription.current_price,
            amount_paid=Decimal(str(data.amount)) if data.amount else subscription.current_price,
            status=PaymentStatus.COMPLETED,
            payment_method=data.payment_method,
            payment_reference=data.reference or f"MANUAL-USER-{subscription.id}",
            period_start=subscription.start_date,
            period_end=subscription.end_date,
            description=f"Activation manuelle utilisateur - {subscription.plan_name}",
            notes=data.notes,
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        db.commit()
        db.refresh(subscription)
        
        response = {
            "message": "Abonnement utilisateur activé manuellement avec succès",
            "success": True,
            "subscription": {
                "id": str(subscription.id),
                "user_id": str(user.id),
                "user_email": user.email,
                "user_name": user.nom_complet,
                "tenant_id": str(user.tenant_id),
                "tenant_name": user.tenant.nom_pharmacie if user.tenant else None,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "status": subscription.status.value,
                "billing_period": subscription.billing_period.value,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining(),
                "current_price": float(subscription.current_price),
                "activated_by": current_user.email,
                "activated_at": datetime.utcnow().isoformat()
            },
            "payment": {
                "id": str(payment.id),
                "amount": float(payment.amount),
                "payment_method": payment.payment_method.value,
                "reference": payment.payment_reference
            }
        }
        
        logger.info(f"Activation manuelle réussie pour utilisateur {user.email} (plan: {data.plan.value})")
        return response
        
    except ValueError as e:
        db.rollback()
        logger.error(f"Erreur de validation: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_request", "message": str(e)}
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur inattendue: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "activation_failed", "message": "Erreur lors de l'activation"}
        )


# =============================================================================
# GESTION DES TENANTS - DÉTAILS DES ABONNEMENTS
# =============================================================================

@router.get("/tenant/{tenant_id}", response_model=Dict[str, Any])
async def get_tenant_subscriptions(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Récupère tous les abonnements d'un tenant spécifique.
    Inclut l'abonnement principal du tenant et les abonnements utilisateurs.
    
    Args:
        tenant_id: ID du tenant
        
    Returns:
        Abonnement du tenant et liste des abonnements utilisateurs
    """
    logger.info(f"Demande des abonnements du tenant {tenant_id} par {current_user.email}")
    
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "tenant_not_found", "message": "Tenant non trouvé"}
            )
        
        # Abonnement principal du tenant (celui du propriétaire)
        main_subscription = None
        if tenant.owner_id:
            main_sub = db.query(Subscription).filter(
                Subscription.tenant_id == tenant_id,
                Subscription.user_id == tenant.owner_id
            ).first()
            
            if main_sub:
                main_subscription = {
                    "id": str(main_sub.id),
                    "plan": main_sub.plan.value,
                    "plan_name": main_sub.plan_name,
                    "status": main_sub.status.value,
                    "billing_period": main_sub.billing_period.value,
                    "is_active": main_sub.is_active(),
                    "is_trial": main_sub.is_trial(),
                    "start_date": main_sub.start_date.isoformat() if main_sub.start_date else None,
                    "end_date": main_sub.end_date.isoformat() if main_sub.end_date else None,
                    "trial_end_date": main_sub.trial_end_date.isoformat() if main_sub.trial_end_date else None,
                    "days_remaining": main_sub.days_remaining(),
                    "current_price": float(main_sub.current_price),
                    "auto_renew": main_sub.auto_renew,
                    "max_users": main_sub.max_users,
                    "max_products": main_sub.max_products,
                    "max_storage_mb": main_sub.max_storage_mb
                }
        
        # Abonnements des autres utilisateurs du tenant
        user_subs = []
        users = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.id != tenant.owner_id  # Exclure le propriétaire (déjà dans main_subscription)
        ).all()
        
        for user in users:
            sub = user.subscription
            if sub:
                user_subs.append({
                    "user_id": str(user.id),
                    "user_email": user.email,
                    "user_name": user.nom_complet,
                    "user_role": user.role,
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "is_active": sub.is_active(),
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining(),
                    "current_price": float(sub.current_price)
                })
        
        return {
            "tenant_id": str(tenant_id),
            "tenant_name": tenant.nom_pharmacie,
            "tenant_code": tenant.tenant_code,
            "owner_email": tenant.owner.email if tenant.owner else None,
            "main_subscription": main_subscription,
            "user_subscriptions": user_subs,
            "total_users_with_subscription": len(user_subs) + (1 if main_subscription else 0),
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "fetch_failed", "message": "Erreur lors de la récupération"}
        )


# =============================================================================
# PROLONGATION D'ESSAI - TENANT
# =============================================================================

@router.post("/extend-trial/tenant/{tenant_id}", response_model=Dict[str, Any])
async def extend_tenant_trial(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    extra_days: int = Query(7, ge=1, le=30, description="Nombre de jours supplémentaires (1-30)")
):
    """
    Prolonge la période d'essai d'un tenant.
    
    Args:
        tenant_id: ID du tenant
        extra_days: Nombre de jours supplémentaires
        
    Returns:
        Confirmation de la prolongation
    """
    logger.info(f"Prolongation d'essai tenant demandée par {current_user.email} pour {tenant_id} (+{extra_days} jours)")
    
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "tenant_not_found", "message": "Tenant non trouvé"}
            )
        
        if not tenant.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "no_owner", "message": "Le tenant n'a pas de propriétaire"}
            )
        
        subscription = db.query(Subscription).filter(
            Subscription.tenant_id == tenant_id,
            Subscription.user_id == tenant.owner_id
        ).first()
        
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "no_subscription", "message": "Aucun abonnement trouvé pour ce tenant"}
            )
        
        if subscription.status != SubscriptionStatus.TRIAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "not_in_trial",
                    "message": "Le tenant n'est pas en période d'essai",
                    "current_status": subscription.status.value
                }
            )
        
        # Prolonger l'essai
        old_end = subscription.end_date
        new_end = old_end + timedelta(days=extra_days)
        
        subscription.end_date = new_end
        subscription.trial_end_date = new_end
        
        # Ajouter une trace dans les métadonnées
        import json
        meta_data = {}
        if subscription.meta_data:
            try:
                meta_data = json.loads(subscription.meta_data)
            except:
                pass
        
        meta_data["trial_extended"] = {
            "extended_by": str(current_user.id),
            "extended_by_email": current_user.email,
            "extended_at": datetime.utcnow().isoformat(),
            "extra_days": extra_days,
            "old_end_date": old_end.isoformat(),
            "new_end_date": new_end.isoformat()
        }
        subscription.meta_data = json.dumps(meta_data)
        subscription.updated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Prolongation d'essai réussie pour tenant {tenant.nom_pharmacie}: {old_end} -> {new_end}")
        
        return {
            "message": f"Période d'essai du tenant prolongée de {extra_days} jours",
            "success": True,
            "tenant_id": str(tenant.id),
            "tenant_name": tenant.nom_pharmacie,
            "old_end_date": old_end.isoformat(),
            "new_end_date": new_end.isoformat(),
            "days_remaining": subscription.days_remaining(),
            "extended_by": current_user.email,
            "extended_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de la prolongation d'essai: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "extension_failed", "message": "Erreur lors de la prolongation"}
        )


# =============================================================================
# PROLONGATION D'ESSAI - UTILISATEUR
# =============================================================================

@router.post("/extend-trial/user/{user_id}", response_model=Dict[str, Any])
async def extend_user_trial(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    extra_days: int = Query(7, ge=1, le=30, description="Nombre de jours supplémentaires (1-30)")
):
    """
    Prolonge la période d'essai d'un utilisateur.
    
    Args:
        user_id: ID de l'utilisateur
        extra_days: Nombre de jours supplémentaires
        
    Returns:
        Confirmation de la prolongation
    """
    logger.info(f"Prolongation d'essai utilisateur demandée par {current_user.email} pour {user_id} (+{extra_days} jours)")
    
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "user_not_found", "message": "Utilisateur non trouvé"}
            )
        
        subscription = user.subscription
        if not subscription:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "no_subscription", "message": "L'utilisateur n'a pas d'abonnement"}
            )
        
        if subscription.status != SubscriptionStatus.TRIAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "not_in_trial",
                    "message": "L'utilisateur n'est pas en période d'essai",
                    "current_status": subscription.status.value
                }
            )
        
        # Prolonger l'essai
        old_end = subscription.end_date
        new_end = old_end + timedelta(days=extra_days)
        
        subscription.end_date = new_end
        subscription.trial_end_date = new_end
        
        # Ajouter une trace dans les métadonnées
        import json
        meta_data = {}
        if subscription.meta_data:
            try:
                meta_data = json.loads(subscription.meta_data)
            except:
                pass
        
        meta_data["trial_extended"] = {
            "extended_by": str(current_user.id),
            "extended_by_email": current_user.email,
            "extended_at": datetime.utcnow().isoformat(),
            "extra_days": extra_days,
            "old_end_date": old_end.isoformat(),
            "new_end_date": new_end.isoformat()
        }
        subscription.meta_data = json.dumps(meta_data)
        subscription.updated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Prolongation d'essai réussie pour utilisateur {user.email}: {old_end} -> {new_end}")
        
        return {
            "message": f"Période d'essai de l'utilisateur prolongée de {extra_days} jours",
            "success": True,
            "user_id": str(user.id),
            "user_email": user.email,
            "user_name": user.nom_complet,
            "old_end_date": old_end.isoformat(),
            "new_end_date": new_end.isoformat(),
            "days_remaining": subscription.days_remaining(),
            "extended_by": current_user.email,
            "extended_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de la prolongation d'essai: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "extension_failed", "message": "Erreur lors de la prolongation"}
        )


# =============================================================================
# STATISTIQUES AVANCÉES
# =============================================================================

@router.get("/statistics", response_model=Dict[str, Any])
async def get_subscription_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Statistiques avancées sur les abonnements.
    """
    logger.info(f"Demande de statistiques avancées par {current_user.email}")
    
    try:
        # Statistiques des abonnements tenants
        tenant_subs = db.query(Subscription).join(Tenant, Subscription.tenant_id == Tenant.id)
        
        total_tenant_subs = tenant_subs.count()
        active_tenant_subs = tenant_subs.filter(Subscription.status == SubscriptionStatus.ACTIVE).count()
        trial_tenant_subs = tenant_subs.filter(Subscription.status == SubscriptionStatus.TRIAL).count()
        
        # Statistiques des abonnements utilisateurs (hors propriétaires)
        user_subs = db.query(Subscription).join(User).filter(User.role != "admin")
        total_user_subs = user_subs.count()
        active_user_subs = user_subs.filter(Subscription.status == SubscriptionStatus.ACTIVE).count()
        trial_user_subs = user_subs.filter(Subscription.status == SubscriptionStatus.TRIAL).count()
        
        # Répartition par plan
        plans_distribution = {}
        for plan in SubscriptionPlan:
            count = db.query(Subscription).filter(Subscription.plan == plan).count()
            if count > 0:
                plans_distribution[plan.value] = count
        
        # Calcul des revenus
        active_subs = db.query(Subscription).filter(Subscription.status == SubscriptionStatus.ACTIVE).all()
        monthly_revenue = sum([float(sub.current_price) for sub in active_subs])
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "tenants": {
                "total_subscriptions": total_tenant_subs,
                "active": active_tenant_subs,
                "trial": trial_tenant_subs,
                "conversion_rate": round(active_tenant_subs / max(trial_tenant_subs + active_tenant_subs, 1) * 100, 2)
            },
            "users": {
                "total_subscriptions": total_user_subs,
                "active": active_user_subs,
                "trial": trial_user_subs,
                "conversion_rate": round(active_user_subs / max(trial_user_subs + active_user_subs, 1) * 100, 2)
            },
            "plans_distribution": plans_distribution,
            "revenue": {
                "monthly_recurring": round(monthly_revenue, 2),
                "annual_projected": round(monthly_revenue * 12, 2)
            },
            "plans_config": {k.value: v for k, v in PLAN_CONFIG.items()}
        }
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul des statistiques: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "statistics_failed", "message": "Erreur lors du calcul des statistiques"}
        )


# =============================================================================
# RECHERCHE ET FILTRES
# =============================================================================

@router.get("/search", response_model=Dict[str, Any])
async def search_subscriptions(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    query: str = Query(..., min_length=2, description="Terme de recherche"),
    subscription_type: Optional[str] = Query(None, regex="^(tenant|user)$", description="Type d'abonnement"),
    status: Optional[str] = Query(None, regex="^(active|trial|expired|suspended|cancelled)$")
):
    """
    Recherche d'abonnements par email, nom ou tenant.
    """
    logger.info(f"Recherche d'abonnements: '{query}' par {current_user.email}")
    
    try:
        search_pattern = f"%{query.lower()}%"
        results = []
        
        # Recherche d'abonnements tenants
        if not subscription_type or subscription_type == "tenant":
            tenant_subs = db.query(Subscription).join(Tenant).filter(
                or_(
                    Tenant.nom_pharmacie.ilike(search_pattern),
                    Tenant.tenant_code.ilike(search_pattern),
                    Tenant.email.ilike(search_pattern)
                )
            )
            
            if status:
                tenant_subs = tenant_subs.filter(Subscription.status == status)
            
            for sub in tenant_subs.limit(50).all():
                tenant = sub.tenant
                results.append({
                    "type": "tenant",
                    "tenant_id": str(tenant.id) if tenant else None,
                    "tenant_name": tenant.nom_pharmacie if tenant else None,
                    "tenant_code": tenant.tenant_code if tenant else None,
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "is_active": sub.is_active(),
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining()
                })
        
        # Recherche d'abonnements utilisateurs
        if not subscription_type or subscription_type == "user":
            user_subs = db.query(Subscription).join(User).filter(
                or_(
                    User.email.ilike(search_pattern),
                    User.nom_complet.ilike(search_pattern)
                )
            )
            
            if status:
                user_subs = user_subs.filter(Subscription.status == status)
            
            for sub in user_subs.limit(50).all():
                user = sub.user
                results.append({
                    "type": "user",
                    "user_id": str(user.id) if user else None,
                    "user_email": user.email if user else None,
                    "user_name": user.nom_complet if user else None,
                    "tenant_id": str(user.tenant_id) if user and user.tenant_id else None,
                    "tenant_name": user.tenant.nom_pharmacie if user and user.tenant else None,
                    "subscription_id": str(sub.id),
                    "plan": sub.plan.value,
                    "plan_name": sub.plan_name,
                    "status": sub.status.value,
                    "is_active": sub.is_active(),
                    "end_date": sub.end_date.isoformat() if sub.end_date else None,
                    "days_remaining": sub.days_remaining()
                })
        
        return {
            "query": query,
            "filters": {
                "subscription_type": subscription_type,
                "status": status
            },
            "total_results": len(results),
            "results": results,
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "search_failed", "message": "Erreur lors de la recherche"}
        )


# =============================================================================
# ENDPOINT DE SANTÉ
# =============================================================================

@router.get("/health")
async def health_check():
    """
    Vérifie que le service est opérationnel.
    """
    return {
        "status": "healthy",
        "service": "super-admin-subscriptions-api",
        "timestamp": datetime.utcnow().isoformat(),
        "plans_available": [plan.value for plan in SubscriptionPlan]
    }