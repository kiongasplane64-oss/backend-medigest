# app/api/v1/superadmin_subscriptions.py
"""
Endpoints de gestion des abonnements pour les super administrateurs.
Permet l'activation manuelle, la prolongation d'essais et la vue d'ensemble.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional, Dict, Any, List
from uuid import UUID
import logging
from datetime import datetime, timedelta

from app.api.deps import get_db, verify_super_admin
from app.models.user import User
from app.models.tenant import Tenant  # <-- IMPORT AJOUTÉ
from app.services.subscription_service import (
    get_subscription_summary_for_superadmin,
    upgrade_subscription,
    PLAN_CONFIG
)
from app.schemas.subscription import ManualActivationSchema, SubscriptionFilterSchema

# Configuration du logger
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/super-admin/subscriptions", tags=["Super Admin - Subscriptions"])


# =============================================================================
# VUE D'ENSEMBLE
# =============================================================================

@router.get("/overview", response_model=Dict[str, Any])
async def get_subscriptions_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    tenant_id: Optional[str] = Query(None, description="Filtrer par ID de tenant (optionnel)")
) -> Dict[str, Any]:
    """
    Vue d'ensemble des abonnements pour le super admin.
    
    Args:
        tenant_id: ID du tenant pour filtrer (optionnel)
        
    Returns:
        Statistiques globales et liste détaillée des abonnements
    """
    logger.info(f"Demande de vue d'ensemble des abonnements par {current_user.email}")
    
    try:
        summary = get_subscription_summary_for_superadmin(db, tenant_id)
        
        # Ajouter des statistiques globales
        summary["total_revenue_monthly"] = sum([
            PLAN_CONFIG.get(t["plan"], {}).get("price_monthly", 0) 
            for t in summary["tenants"] 
            if t.get("is_active", False) and t.get("plan") != "trial"
        ])
        
        summary["total_revenue_yearly"] = summary["total_revenue_monthly"] * 12
        
        # Ajouter des métadonnées
        summary["requested_by"] = current_user.email
        summary["requested_at"] = datetime.utcnow().isoformat()
        summary["filter"] = {"tenant_id": tenant_id} if tenant_id else None
        
        logger.info(f"Vue d'ensemble récupérée: {summary.get('total_tenants', 0)} tenants")
        return summary
        
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
# ACTIVATION MANUELLE (PAIEMENT CASH)
# =============================================================================

@router.post("/manual-activation", response_model=Dict[str, Any])
async def manual_activate_subscription(
    data: ManualActivationSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Activation manuelle d'un abonnement (paiement cash).
    Réservé aux super administrateurs pour les paiements hors ligne.
    
    Args:
        data: Informations d'activation manuelle (utilisateur, plan, montant)
        
    Returns:
        Confirmation de l'activation avec détails de l'abonnement
    """
    logger.info(f"Activation manuelle d'abonnement par {current_user.email} pour l'utilisateur {data.user_id}")
    
    # Vérifier que l'utilisateur existe
    user = db.query(User).filter(User.id == data.user_id).first()
    if not user:
        logger.warning(f"Tentative d'activation pour utilisateur inexistant: {data.user_id}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "user_not_found",
                "message": "Utilisateur non trouvé"
            }
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
            activated_by=str(current_user.id)
        )
        
        response = {
            "message": "Abonnement activé manuellement avec succès",
            "success": True,
            "subscription": {
                "id": str(subscription.id),
                "user_id": str(subscription.user_id),
                "user_email": user.email,
                "tenant_id": str(subscription.tenant_id),
                "plan": subscription.plan_type,
                "plan_name": subscription.plan_name,
                "status": subscription.status,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": subscription.days_remaining(),
                "activated_by": current_user.email,
                "activated_at": datetime.utcnow().isoformat()
            },
            "receipt": {
                "amount": float(subscription.price),
                "currency": subscription.currency,
                "payment_method": data.payment_method,
                "reference": data.reference or f"MANUAL-{subscription.id}",
                "notes": data.notes
            }
        }
        
        logger.info(f"Activation manuelle réussie pour {user.email} (plan: {data.plan})")
        return response
        
    except ValueError as e:
        logger.error(f"Erreur de validation lors de l'activation manuelle: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_request",
                "message": str(e)
            }
        )
    except Exception as e:
        logger.error(f"Erreur inattendue lors de l'activation manuelle: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation manuelle. Veuillez réessayer."
            }
        )


# =============================================================================
# GESTION DES TENANTS
# =============================================================================

@router.get("/tenant/{tenant_id}", response_model=Dict[str, Any])
async def get_tenant_subscriptions(
    tenant_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin)
):
    """
    Récupère tous les abonnements d'un tenant spécifique.
    
    Args:
        tenant_id: ID du tenant
        
    Returns:
        Liste des abonnements de tous les utilisateurs du tenant
    """
    logger.info(f"Demande des abonnements du tenant {tenant_id} par {current_user.email}")
    
    try:
        users = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.actif == True
        ).all()
        
        result = []
        for user in users:
            if user.subscription:
                result.append({
                    "user_id": str(user.id),
                    "user_email": user.email,
                    "user_role": user.role,
                    "subscription": {
                        "id": str(user.subscription.id),
                        "plan": user.subscription.plan_type,
                        "plan_name": user.subscription.plan_name,
                        "status": user.subscription.status,
                        "is_active": user.subscription.is_active(),
                        "start_date": user.subscription.start_date.isoformat() if user.subscription.start_date else None,
                        "end_date": user.subscription.end_date.isoformat() if user.subscription.end_date else None,
                        "days_remaining": user.subscription.days_remaining(),
                        "price": float(user.subscription.price) if user.subscription.price else 0
                    }
                })
        
        logger.info(f"Abonnements récupérés pour le tenant {tenant_id}: {len(result)} utilisateurs")
        
        return {
            "tenant_id": str(tenant_id),
            "total_users": len(users),
            "users_with_subscription": len(result),
            "subscriptions": result,
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des abonnements du tenant: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "fetch_failed",
                "message": "Erreur lors de la récupération des données"
            }
        )


# =============================================================================
# PROLONGATION D'ESSAI
# =============================================================================

@router.post("/extend-trial/{user_id}", response_model=Dict[str, Any])
async def extend_trial_period(
    user_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    extra_days: int = Query(7, ge=1, le=30, description="Nombre de jours supplémentaires (1-30)")
):
    """
    Prolonge la période d'essai d'un utilisateur.
    
    Args:
        user_id: ID de l'utilisateur
        extra_days: Nombre de jours supplémentaires (défaut: 7, max: 30)
        
    Returns:
        Confirmation de la prolongation avec les nouvelles dates
    """
    logger.info(f"Prolongation d'essai demandée par {current_user.email} pour {user_id} (+{extra_days} jours)")
    
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "user_not_found",
                    "message": "Utilisateur non trouvé"
                }
            )
        
        if not user.subscription:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "no_subscription",
                    "message": "L'utilisateur n'a pas d'abonnement"
                }
            )
        
        if user.subscription.plan_type != "trial":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "not_in_trial",
                    "message": "L'utilisateur n'est pas en période d'essai",
                    "current_plan": user.subscription.plan_type
                }
            )
        
        # Prolonger l'essai
        old_end = user.subscription.end_date
        if not old_end:
            old_end = datetime.utcnow()
        
        new_end = old_end + timedelta(days=extra_days)
        
        user.subscription.end_date = new_end
        user.subscription.trial_end_date = new_end
        
        # Ajouter une trace dans la config
        if not user.subscription.config:
            user.subscription.config = {}
        
        user.subscription.config["trial_extended"] = {
            "extended_by": str(current_user.id),
            "extended_by_email": current_user.email,
            "extended_at": datetime.utcnow().isoformat(),
            "extra_days": extra_days,
            "old_end_date": old_end.isoformat() if old_end else None,
            "new_end_date": new_end.isoformat()
        }
        
        db.commit()
        
        logger.info(f"Prolongation d'essai réussie pour {user.email}: {old_end} -> {new_end}")
        
        return {
            "message": f"Période d'essai prolongée de {extra_days} jours",
            "success": True,
            "user_id": str(user.id),
            "user_email": user.email,
            "old_end_date": old_end.isoformat() if old_end else None,
            "new_end_date": new_end.isoformat(),
            "days_remaining": user.subscription.days_remaining(),
            "extended_by": current_user.email,
            "extended_at": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors de la prolongation d'essai: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "extension_failed",
                "message": "Erreur lors de la prolongation de l'essai"
            }
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
    
    Returns:
        Statistiques détaillées (conversion, rétention, revenus)
    """
    logger.info(f"Demande de statistiques avancées par {current_user.email}")
    
    try:
        summary = get_subscription_summary_for_superadmin(db)
        
        # Calculer des métriques avancées
        total_tenants = summary.get("total_tenants", 0)
        trial_count = summary.get("trial_subscriptions", 0)
        active_count = summary.get("active_subscriptions", 0)
        
        # Taux de conversion essai -> payant
        conversion_rate = 0
        if trial_count + active_count > 0:
            conversion_rate = round((active_count / (trial_count + active_count)) * 100, 2)
        
        # Répartition par plan
        plans_distribution = summary.get("plans_distribution", {})
        
        # Revenus projetés
        monthly_revenue = sum([
            PLAN_CONFIG.get(plan, {}).get("price_monthly", 0) * count
            for plan, count in plans_distribution.items()
            if plan != "trial"
        ])
        
        yearly_revenue = monthly_revenue * 12
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "summary": {
                "total_tenants": total_tenants,
                "trial_tenants": trial_count,
                "active_paid_tenants": active_count,
                "conversion_rate": f"{conversion_rate}%"
            },
            "distribution": plans_distribution,
            "revenue": {
                "monthly": monthly_revenue,
                "yearly": yearly_revenue,
                "average_per_tenant": round(monthly_revenue / max(active_count, 1), 2)
            },
            "plans_config": PLAN_CONFIG
        }
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul des statistiques: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "statistics_failed",
                "message": "Erreur lors du calcul des statistiques"
            }
        )


# =============================================================================
# RECHERCHE ET FILTRES
# =============================================================================

@router.get("/search", response_model=Dict[str, Any])
async def search_subscriptions(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_super_admin),
    query: str = Query(..., min_length=2, description="Terme de recherche"),
    status: Optional[str] = Query(None, regex="^(active|expired|trial)$")
):
    """
    Recherche d'abonnements par email, nom ou tenant.
    
    Args:
        query: Terme de recherche (email, nom, tenant_code)
        status: Filtrer par statut (optionnel)
        
    Returns:
        Résultats de la recherche
    """
    logger.info(f"Recherche d'abonnements: '{query}' par {current_user.email}")
    
    try:
        search_pattern = f"%{query.lower()}%"
        
        # Rechercher les utilisateurs avec abonnements
        users_query = db.query(User).join(User.subscription).filter(
            or_(
                User.email.ilike(search_pattern),
                User.nom_complet.ilike(search_pattern),
                User.tenant.has(Tenant.nom_pharmacie.ilike(search_pattern)),
                User.tenant.has(Tenant.tenant_code.ilike(search_pattern))
            )
        )
        
        if status:
            users_query = users_query.filter(User.subscription.has(status=status))
        
        users = users_query.limit(50).all()
        
        results = []
        for user in users:
            if user.subscription:
                results.append({
                    "user_id": str(user.id),
                    "user_email": user.email,
                    "user_name": user.nom_complet,
                    "tenant_id": str(user.tenant_id) if user.tenant_id else None,
                    "tenant_name": user.tenant.nom_pharmacie if user.tenant else None,
                    "subscription": {
                        "id": str(user.subscription.id),
                        "plan": user.subscription.plan_type,
                        "status": user.subscription.status,
                        "end_date": user.subscription.end_date.isoformat() if user.subscription.end_date else None,
                        "days_remaining": user.subscription.days_remaining()
                    }
                })
        
        return {
            "query": query,
            "filters": {"status": status},
            "total_results": len(results),
            "results": results,
            "requested_at": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "search_failed",
                "message": "Erreur lors de la recherche"
            }
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
        "plans_available": list(PLAN_CONFIG.keys())
    }