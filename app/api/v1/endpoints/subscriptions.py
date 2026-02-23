# app/api/v1/endpoints/subscription.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.tenant import Tenant, Subscription
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCreate, SubscriptionUpdate, 
    SubscriptionInDB, SubscriptionResponse
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission

router = APIRouter(prefix="/tenants/{tenant_id}/subscription", tags=["subscriptions"])
logger = logging.getLogger(__name__)

@router.get("/", response_model=SubscriptionInDB, summary="Détails de l'abonnement")
@require_permission("view_subscription")
async def get_subscription(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les détails de l'abonnement du tenant.
    """
    # Vérifier si l'utilisateur a accès à ce tenant
    if str(current_user.tenant_id) != str(tenant_id) and current_user.role not in ['admin', 'super_admin']:
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    # Récupérer l'abonnement
    subscription = db.query(Subscription).filter(
        Subscription.tenant_id == tenant_id
    ).first()
    
    if not subscription:
        # Retourner un abonnement par défaut si aucun n'existe
        return SubscriptionInDB(
            tenant_id=tenant_id,
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
            is_active=True
        )
    
    return SubscriptionInDB.from_orm(subscription)

@router.post("/", response_model=SubscriptionResponse, summary="Créer/mettre à jour un abonnement")
@require_permission("manage_subscription")
async def create_or_update_subscription(
    tenant_id: UUID,
    subscription_data: SubscriptionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crée ou met à jour un abonnement pour un tenant.
    """
    # Vérifier les permissions
    if current_user.role not in ['admin', 'super_admin']:
        raise HTTPException(status_code=403, detail="Permission insuffisante")
    
    # Vérifier si le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant non trouvé")
    
    # Vérifier si un abonnement existe déjà
    existing_subscription = db.query(Subscription).filter(
        Subscription.tenant_id == tenant_id
    ).first()
    
    try:
        if existing_subscription:
            # Mettre à jour l'abonnement existant
            update_data = subscription_data.dict(exclude_unset=True)
            for field, value in update_data.items():
                setattr(existing_subscription, field, value)
            
            db.commit()
            db.refresh(existing_subscription)
            
            return SubscriptionResponse(
                message="Abonnement mis à jour avec succès",
                subscription=SubscriptionInDB.from_orm(existing_subscription)
            )
        else:
            # Créer un nouvel abonnement
            subscription = Subscription(
                **subscription_data.dict(),
                tenant_id=tenant_id
            )
            
            db.add(subscription)
            db.commit()
            db.refresh(subscription)
            
            return SubscriptionResponse(
                message="Abonnement créé avec succès",
                subscription=SubscriptionInDB.from_orm(subscription)
            )
            
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur gestion abonnement: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.get("/plans", summary="Liste des plans disponibles")
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
            "features": ["gestion_stock_basique", "rapports_limites"]
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
            "features": ["gestion_stock_complete", "ventes_pos", "rapports_détaillés", "support_email"]
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
            "features": ["toutes_fonctions_standard", "analyses_avancées", "multi_pharmacies", "support_prioritaire"]
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
            "features": ["toutes_fonctions_pro", "api_integration", "formation", "support_dédié"]
        }
    ]
    
    return {"plans": plans}