# app/api/v1/subscriptions.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
import logging
from datetime import datetime, date

from app.db.session import get_db
from app.models.subscription import Subscription
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate, SubscriptionUpdate, 
    SubscriptionInDB, SubscriptionResponse
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.subscription_service import get_active_subscription

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])
logger = logging.getLogger(__name__)

# Routes compatibles avec l'ancienne interface
@router.post("/", summary="Créer un abonnement")
@require_permission("manage_subscription")
async def create_subscription(
    data: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un abonnement mensuel.
    """
    try:
        sub = Subscription(
            tenant_id=current_user.tenant_id,
            plan_name=data.plan_name or "Standard",
            plan_type=data.plan_type or "standard",
            price=data.price,
            billing_cycle="monthly",
            current_period_start=datetime.utcnow(),
            current_period_end=datetime.utcnow().replace(month=datetime.utcnow().month + 1)
        )
        
        db.add(sub)
        db.commit()
        db.refresh(sub)
        
        return {
            "message": "Abonnement mensuel activé",
            "date_fin": sub.current_period_end,
            "subscription": SubscriptionInDB.from_orm(sub)
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création abonnement: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.get("/status", summary="Statut de l'abonnement")
async def subscription_status(
    tenant_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le statut de l'abonnement.
    """
    try:
        # Utiliser l'ID du tenant fourni ou celui de l'utilisateur courant
        target_tenant_id = tenant_id or current_user.tenant_id
        
        # Récupérer l'abonnement actif
        sub = db.query(Subscription).filter(
            Subscription.tenant_id == target_tenant_id,
            Subscription.is_active == True
        ).first()
        
        if not sub or sub.current_period_end < datetime.utcnow().date():
            return {
                "active": False,
                "mode": "READ_ONLY",
                "message": "Abonnement expiré ou inexistant"
            }

        return {
            "active": True,
            "mode": "FULL",
            "subscription": SubscriptionInDB.from_orm(sub),
            "days_remaining": (sub.current_period_end - datetime.utcnow().date()).days
        }
        
    except Exception as e:
        logger.error(f"Erreur vérification statut abonnement: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

# Nouvelles routes pour la gestion complète
@router.get("/current", response_model=SubscriptionInDB, summary="Abonnement actuel")
@require_permission("view_subscription")
async def get_current_subscription(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'abonnement actuel du tenant.
    """
    subscription = db.query(Subscription).filter(
        Subscription.tenant_id == current_tenant.id,
        Subscription.is_active == True
    ).first()
    
    if not subscription:
        # Retourner un abonnement par défaut
        return SubscriptionInDB(
            id=UUID("00000000-0000-0000-0000-000000000000"),
            tenant_id=current_tenant.id,
            plan_name="Gratuit",
            plan_type="free",
            status="active",
            max_users=1,
            max_products=100,
            features={},
            billing_cycle="monthly",
            price=0.0,
            currency="USD",
            current_period_start=None,
            current_period_end=None,
            is_active=True,
            notes="Plan gratuit par défaut",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
    
    return SubscriptionInDB.from_orm(subscription)

@router.put("/", response_model=SubscriptionResponse, summary="Mettre à jour l'abonnement")
@require_permission("manage_subscription")
async def update_subscription(
    subscription_data: SubscriptionUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Met à jour l'abonnement du tenant.
    """
    try:
        subscription = db.query(Subscription).filter(
            Subscription.tenant_id == current_tenant.id
        ).first()
        
        if not subscription:
            # Créer un nouvel abonnement
            subscription = Subscription(
                **subscription_data.dict(exclude_unset=True),
                tenant_id=current_tenant.id
            )
            db.add(subscription)
            message = "Abonnement créé avec succès"
        else:
            # Mettre à jour l'abonnement existant
            update_data = subscription_data.dict(exclude_unset=True)
            for field, value in update_data.items():
                setattr(subscription, field, value)
            message = "Abonnement mis à jour avec succès"
        
        db.commit()
        db.refresh(subscription)
        
        return SubscriptionResponse(
            message=message,
            subscription=SubscriptionInDB.from_orm(subscription)
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour abonnement: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.get("/plans", summary="Plans disponibles")
async def get_available_plans():
    """
    Récupère la liste des plans d'abonnement disponibles.
    """
    plans = [
        {
            "name": "Gratuit",
            "type": "free",
            "description": "Plan de base gratuit",
            "max_users": 1,
            "max_products": 100,
            "price": 0.0,
            "currency": "USD",
            "billing_cycle": "monthly",
            "features": [
                "gestion_stock_basique",
                "rapports_limites",
                "1 utilisateur"
            ]
        },
        {
            "name": "Standard",
            "type": "standard",
            "description": "Plan standard pour petites pharmacies",
            "max_users": 3,
            "max_products": 1000,
            "price": 49.99,
            "currency": "USD",
            "billing_cycle": "monthly",
            "features": [
                "gestion_stock_complete",
                "ventes_pos",
                "rapports_détaillés",
                "support_email",
                "3 utilisateurs"
            ]
        },
        {
            "name": "Pro",
            "type": "pro",
            "description": "Plan professionnel pour pharmacies moyennes",
            "max_users": 10,
            "max_products": 5000,
            "price": 99.99,
            "currency": "USD",
            "billing_cycle": "monthly",
            "features": [
                "toutes_fonctions_standard",
                "analyses_avancées",
                "multi_pharmacies",
                "support_prioritaire",
                "10 utilisateurs"
            ]
        },
        {
            "name": "Enterprise",
            "type": "enterprise",
            "description": "Solution complète pour grandes pharmacies",
            "max_users": "Illimité",
            "max_products": "Illimité",
            "price": 199.99,
            "currency": "USD",
            "billing_cycle": "monthly",
            "features": [
                "toutes_fonctions_pro",
                "api_integration",
                "formation",
                "support_dédié",
                "utilisateurs illimités"
            ]
        }
    ]
    
    return {"plans": plans}

@router.get("/usage", summary="Utilisation actuelle")
@require_permission("view_subscription")
async def get_current_usage(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère l'utilisation actuelle par rapport aux limites de l'abonnement.
    """
    # Récupérer l'abonnement
    subscription = db.query(Subscription).filter(
        Subscription.tenant_id == current_tenant.id,
        Subscription.is_active == True
    ).first()
    
    if not subscription:
        # Utiliser les limites du plan gratuit
        max_users = 1
        max_products = 100
    else:
        max_users = subscription.max_users or 1
        max_products = subscription.max_products or 100
    
    # Compter les utilisateurs actifs
    user_count = db.query(User).filter(
        User.tenant_id == current_tenant.id,
        User.is_active == True
    ).count()
    
    # Compter les produits actifs
    from app.models.product import Product
    product_count = db.query(Product).filter(
        Product.tenant_id == current_tenant.id,
        Product.is_active == True
    ).count()
    
    return {
        "subscription": SubscriptionInDB.from_orm(subscription) if subscription else None,
        "usage": {
            "users": {
                "current": user_count,
                "max": max_users,
                "percentage": min(100, (user_count / max_users) * 100) if max_users > 0 else 0
            },
            "products": {
                "current": product_count,
                "max": max_products,
                "percentage": min(100, (product_count / max_products) * 100) if max_products > 0 else 0
            }
        }
    }

# Service de compatibilité
@router.get("/{tenant_id}/status", summary="Statut d'abonnement par tenant ID")
async def get_subscription_by_tenant(
    tenant_id: UUID,
    db: Session = Depends(get_db)
):
    """
    Récupère le statut de l'abonnement pour un tenant spécifique.
    Compatible avec l'ancienne interface.
    """
    sub = get_active_subscription(tenant_id)
    
    if not sub or (sub.current_period_end and sub.current_period_end < date.today()):
        return {
            "active": False,
            "mode": "READ_ONLY",
            "message": "Abonnement expiré ou inexistant"
        }

    return {
        "active": True,
        "mode": "FULL",
        "subscription": SubscriptionInDB.from_orm(sub) if hasattr(sub, 'id') else sub
    }