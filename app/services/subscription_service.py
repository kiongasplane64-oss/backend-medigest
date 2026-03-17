# app/services/subscription_service.py
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import logging
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.user import User
from app.models.tenant import Tenant
from app.models.user_subscription import UserSubscription
from app.models.pharmacy import Pharmacy
from app.models.payment import Payment

logger = logging.getLogger(__name__)

# Configuration des plans
PLAN_CONFIG = {
    "trial": {
        "name": "Essai gratuit",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_users_per_tenant": 1,  # L'admin peut créer 1 utilisateur (lui-même)
        "max_products": 100,
        "max_pharmacies": 1,  # L'admin peut créer 1 pharmacie
        "features": [
            "1 utilisateur",
            "100 produits",
            "1 pharmacie",
            "Support email",
            "Période d'essai de 14 jours"
        ]
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 29.99,
        "price_yearly": 299.99,
        "max_users_per_tenant": 2,  # Admin + 1 employé
        "max_products": 500,
        "max_pharmacies": 1,
        "features": [
            "Jusqu'à 2 utilisateurs",
            "500 produits",
            "1 pharmacie",
            "Support email prioritaire",
            "Rapports basiques"
        ]
    },
    "professional": {
        "name": "Professional",
        "price_monthly": 79.99,
        "price_yearly": 799.99,
        "max_users_per_tenant": 5,  # Admin + 4 employés
        "max_products": 0,  # Illimité
        "max_pharmacies": 3,
        "features": [
            "Jusqu'à 5 utilisateurs",
            "Produits illimités",
            "Jusqu'à 3 pharmacies",
            "Support 24/7",
            "Rapports avancés",
            "API d'intégration"
        ]
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": 199.99,
        "price_yearly": 1999.99,
        "max_users_per_tenant": 0,  # Illimité
        "max_products": 0,  # Illimité
        "max_pharmacies": 0,  # Illimité
        "features": [
            "Utilisateurs illimités",
            "Produits illimités",
            "Pharmacies illimitées",
            "Support dédié",
            "Formation sur site",
            "Personnalisation",
            "SLA garanti"
        ]
    }
}


def create_trial_subscription(
    db: Session,
    user_id: str,
    tenant_id: str,
    trial_days: int = 14
) -> UserSubscription:
    """
    Crée un abonnement d'essai pour un nouvel utilisateur
    """
    plan = PLAN_CONFIG["trial"]
    
    start_date = datetime.utcnow()
    end_date = start_date + timedelta(days=trial_days)
    
    subscription = UserSubscription(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_type="trial",
        plan_name=plan["name"],
        start_date=start_date,
        end_date=end_date,
        trial_end_date=end_date,
        status="active",
        price=0,
        billing_cycle="one_time",
        max_users=plan["max_users_per_tenant"],
        max_products=plan["max_products"],
        max_pharmacies=plan["max_pharmacies"],
        auto_renew=False,
        config={
            "trial_days": trial_days,
            "created_at": start_date.isoformat()
        }
    )
    
    db.add(subscription)
    db.commit()
    db.refresh(subscription)
    
    logger.info(f"Abonnement d'essai créé pour l'utilisateur {user_id} (expire le {end_date})")
    
    return subscription


def check_user_subscription(
    db: Session,
    user_id: str
) -> Dict[str, Any]:
    """
    Vérifie le statut de l'abonnement d'un utilisateur
    Met à jour le statut si expiré
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {
            "has_subscription": False,
            "error": "Utilisateur non trouvé"
        }
    
    if not user.subscription:
        return {
            "has_subscription": False,
            "user_id": str(user_id),
            "mode": "READ_ONLY",
            "message": "Aucun abonnement trouvé"
        }
    
    subscription = user.subscription
    
    # Vérifier si l'abonnement a expiré
    if subscription.has_expired() and subscription.status == "active":
        subscription.status = "expired"
        db.commit()
        logger.info(f"Abonnement expiré pour l'utilisateur {user_id}")
        
        return {
            "has_subscription": True,
            "user_id": str(user_id),
            "plan": subscription.plan_type,
            "plan_name": subscription.plan_name,
            "status": "expired",
            "mode": "READ_ONLY",
            "message": f"Abonnement expiré depuis le {subscription.end_date.strftime('%d/%m/%Y')}",
            "expired_date": subscription.end_date.isoformat() if subscription.end_date else None
        }
    
    # Vérifier si c'est un essai qui expire bientôt (alerte)
    warning = None
    if subscription.is_trial():
        days_left = subscription.days_remaining()
        if days_left <= 3:
            warning = {
                "message": f"Votre période d'essai expire dans {days_left} jours",
                "days_remaining": days_left,
                "requires_action": True
            }
    
    return {
        "has_subscription": True,
        "user_id": str(user_id),
        "plan": subscription.plan_type,
        "plan_name": subscription.plan_name,
        "status": subscription.status,
        "mode": subscription.get_mode(),
        "is_active": subscription.is_active(),
        "is_trial": subscription.is_trial(),
        "days_remaining": subscription.days_remaining(),
        "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
        "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
        "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        "warning": warning
    }


def check_tenant_limits(
    db: Session,
    tenant_id: str,
    user_role: str = "admin"
) -> Dict[str, Any]:
    """
    Vérifie les limites du tenant basées sur l'abonnement de l'admin
    """
    # Récupérer l'admin du tenant
    admin = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role == "admin"
    ).first()
    
    if not admin or not admin.subscription:
        return {
            "can_create_user": False,
            "can_create_pharmacy": False,
            "reason": "No active subscription",
            "mode": "READ_ONLY"
        }
    
    subscription = admin.subscription
    
    if not subscription.is_active():
        return {
            "can_create_user": False,
            "can_create_pharmacy": False,
            "reason": "Subscription expired",
            "mode": "READ_ONLY"
        }
    
    # Compter les utilisateurs actuels (hors admin)
    user_count = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.role != "admin",
        User.actif == True
    ).count()
    
    # Compter les pharmacies actuelles
    pharmacy_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).count()
    
    # Déterminer les limites
    max_users = subscription.max_users
    max_pharmacies = subscription.max_pharmacies
    
    can_create_user = max_users == 0 or user_count < max_users
    can_create_pharmacy = max_pharmacies == 0 or pharmacy_count < max_pharmacies
    
    return {
        "tenant_id": str(tenant_id),
        "plan": subscription.plan_type,
        "plan_name": subscription.plan_name,
        "mode": subscription.get_mode(),
        "limits": {
            "max_users": max_users if max_users > 0 else "Illimité",
            "max_products": subscription.max_products if subscription.max_products > 0 else "Illimité",
            "max_pharmacies": max_pharmacies if max_pharmacies > 0 else "Illimité"
        },
        "current_usage": {
            "users": user_count,
            "pharmacies": pharmacy_count
        },
        "can_create_user": can_create_user,
        "can_create_pharmacy": can_create_pharmacy,
        "users_remaining": max_users - user_count if max_users > 0 else "Illimité",
        "pharmacies_remaining": max_pharmacies - pharmacy_count if max_pharmacies > 0 else "Illimité"
    }


def upgrade_subscription(
    db: Session,
    user_id: str,
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None,
    payment_method: Optional[str] = None,
    manual_activation: bool = False,
    activated_by: Optional[str] = None
) -> UserSubscription:
    """
    Met à niveau l'abonnement d'un utilisateur
    Peut être utilisé par le super admin pour activation manuelle
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError("Utilisateur non trouvé")
    
    if new_plan not in PLAN_CONFIG:
        raise ValueError(f"Plan invalide: {new_plan}")
    
    plan_info = PLAN_CONFIG[new_plan]
    
    # Calculer la nouvelle date de fin
    start_date = datetime.utcnow()
    if billing_cycle == "yearly":
        end_date = start_date + timedelta(days=365)
    else:
        end_date = start_date + timedelta(days=30)
    
    # Déterminer le prix
    price = plan_info["price_yearly"] if billing_cycle == "yearly" else plan_info["price_monthly"]
    
    # Créer ou mettre à jour l'abonnement
    if user.subscription:
        subscription = user.subscription
        old_plan = subscription.plan_type
        
        subscription.plan_type = new_plan
        subscription.plan_name = plan_info["name"]
        subscription.start_date = start_date
        subscription.end_date = end_date
        subscription.price = price
        subscription.billing_cycle = billing_cycle
        subscription.payment_id = payment_id
        subscription.status = "active"
        subscription.auto_renew = True
        subscription.max_users = plan_info["max_users_per_tenant"]
        subscription.max_products = plan_info["max_products"]
        subscription.max_pharmacies = plan_info["max_pharmacies"]
        
        # Si c'était un essai, supprimer la date de fin d'essai
        if subscription.plan_type == "trial":
            subscription.trial_end_date = None
        
        # Ajouter des métadonnées
        if not subscription.config:
            subscription.config = {}
        
        subscription.config.update({
            "upgraded_from": old_plan,
            "upgraded_at": start_date.isoformat(),
            "manual_activation": manual_activation,
            "activated_by": activated_by
        })
        
    else:
        subscription = UserSubscription(
            user_id=user_id,
            tenant_id=user.tenant_id,
            plan_type=new_plan,
            plan_name=plan_info["name"],
            start_date=start_date,
            end_date=end_date,
            status="active",
            price=price,
            billing_cycle=billing_cycle,
            max_users=plan_info["max_users_per_tenant"],
            max_products=plan_info["max_products"],
            max_pharmacies=plan_info["max_pharmacies"],
            payment_id=payment_id,
            auto_renew=True,
            config={
                "manual_activation": manual_activation,
                "activated_by": activated_by,
                "activated_at": start_date.isoformat()
            }
        )
        db.add(subscription)
    
    db.commit()
    db.refresh(subscription)
    
    action = "manually activated" if manual_activation else "upgraded"
    logger.info(f"Abonnement {action} pour {user_id}: {new_plan}")
    
    return subscription


def get_subscription_summary_for_superadmin(
    db: Session,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Résumé des abonnements pour le super admin
    """
    query = db.query(User).join(UserSubscription).filter(User.role == "admin")
    
    if tenant_id:
        query = query.filter(User.tenant_id == tenant_id)
    
    admins = query.all()
    
    summary = {
        "total_tenants": len(admins),
        "plans_distribution": {},
        "active_subscriptions": 0,
        "expired_subscriptions": 0,
        "trial_subscriptions": 0,
        "tenants": []
    }
    
    for admin in admins:
        sub = admin.subscription
        if not sub:
            continue
        
        # Compter par plan
        plan = sub.plan_type
        summary["plans_distribution"][plan] = summary["plans_distribution"].get(plan, 0) + 1
        
        if sub.is_active():
            summary["active_subscriptions"] += 1
        else:
            summary["expired_subscriptions"] += 1
        
        if sub.plan_type == "trial":
            summary["trial_subscriptions"] += 1
        
        # Compter les utilisateurs du tenant
        user_count = db.query(User).filter(
            User.tenant_id == admin.tenant_id,
            User.actif == True
        ).count()
        
        pharmacy_count = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == admin.tenant_id,
            Pharmacy.is_active == True
        ).count()
        
        summary["tenants"].append({
            "tenant_id": str(admin.tenant_id),
            "tenant_name": admin.tenant.nom_pharmacie if admin.tenant else "N/A",
            "admin_email": admin.email,
            "plan": sub.plan_type,
            "plan_name": sub.plan_name,
            "status": sub.status,
            "is_active": sub.is_active(),
            "days_remaining": sub.days_remaining(),
            "end_date": sub.end_date.isoformat() if sub.end_date else None,
            "users_count": user_count,
            "pharmacies_count": pharmacy_count,
            "max_users": sub.max_users if sub.max_users > 0 else "Illimité",
            "max_pharmacies": sub.max_pharmacies if sub.max_pharmacies > 0 else "Illimité"
        })
    
    return summary


def process_expired_subscriptions(db: Session) -> int:
    """
    Tâche CRON : Marque les abonnements expirés
    """
    expired_count = 0
    now = datetime.utcnow()
    
    expired_subs = db.query(UserSubscription).filter(
        UserSubscription.status == "active",
        UserSubscription.end_date < now
    ).all()
    
    for sub in expired_subs:
        sub.status = "expired"
        expired_count += 1
        logger.info(f"Abonnement expiré: {sub.id} (utilisateur {sub.user_id})")
    
    if expired_count > 0:
        db.commit()
    
    return expired_count


def can_user_access_feature(user: User, feature: str) -> bool:
    """
    Vérifie si un utilisateur peut accéder à une fonctionnalité spécifique
    """
    if not user.subscription or not user.subscription.is_active():
        return False
    
    # Logique spécifique par fonctionnalité
    if feature == "create_pharmacy":
        return user.role == "admin"
    
    if feature == "add_user":
        return user.role == "admin"
    
    if feature == "export_data":
        return True
    
    return True

def check_subscription_status(db: Session, tenant_id: str) -> bool:
    """
    Vérifie si l'abonnement d'un tenant est actif.
    Compatibilité avec l'ancien code qui utilisait cette fonction.
    
    Args:
        db: Session de base de données
        tenant_id: ID du tenant
        
    Returns:
        True si l'abonnement est actif, False sinon
    """
    from app.models.tenant import Tenant
    from app.models.user import User
    
    try:
        # Récupérer le tenant
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            logger.warning(f"Tenant {tenant_id} non trouvé")
            return False
        
        # Récupérer l'admin du tenant
        admin = db.query(User).filter(
            User.tenant_id == tenant_id,
            User.role == "admin"
        ).first()
        
        if not admin or not admin.subscription:
            logger.warning(f"Aucun abonnement trouvé pour le tenant {tenant_id}")
            return False
        
        # Vérifier si l'abonnement de l'admin est actif
        return admin.subscription.is_active()
        
    except Exception as e:
        logger.error(f"Erreur lors de la vérification de l'abonnement: {e}")
        return False