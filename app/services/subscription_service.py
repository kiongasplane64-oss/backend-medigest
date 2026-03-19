# app/services/subscription_service.py
"""
Service de gestion des abonnements et des codes d'activation.
"""
import logging
import random
import string
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_

# Importer les DEUX modèles
from app.models.subscription import Subscription as TenantSubscription
from app.models.user_subscription import UserSubscription
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.user import User
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION DES PLANS
# ============================================================================

PLAN_CONFIG = {
    "free": {
        "name": "Gratuit",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_users_per_tenant": 1,
        "max_products": 100,
        "max_pharmacies": 1,
        "features": [
            "Gestion de stock de base",
            "Ventes POS",
            "Rapports quotidiens"
        ]
    },
    "starter": {
        "name": "Starter",
        "price_monthly": 5,
        "price_yearly": 48,  # 20% de réduction
        "max_users_per_tenant": 1,
        "max_products": 500,
        "max_pharmacies": 1,
        "features": [
            "1 Utilisateur",
            "Gestion de stock de base",
            "Ventes POS",
            "Rapports quotidiens"
        ]
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 8,
        "price_yearly": 76.8,  # 20% de réduction
        "max_users_per_tenant": 5,
        "max_products": 2000,
        "max_pharmacies": 3,
        "features": [
            "5 Utilisateurs",
            "Transferts inter-stocks",
            "Gestion des dettes",
            "PWA (Mode Offline)",
            "Support Prioritaire"
        ]
    },
    "enterprise": {
        "name": "Entreprise",
        "price_monthly": 15,
        "price_yearly": 144,  # 20% de réduction
        "max_users_per_tenant": 0,  # 0 = Illimité
        "max_products": 0,  # 0 = Illimité
        "max_pharmacies": 0,  # 0 = Illimité
        "features": [
            "Utilisateurs illimités",
            "Analytique avancée",
            "API d'inventaire",
            "Audit Logs complets",
            "Gestion multi-dépôts"
        ]
    },
    "trial": {
        "name": "Essai",
        "price_monthly": 0,
        "price_yearly": 0,
        "max_users_per_tenant": 5,
        "max_products": 500,
        "max_pharmacies": 1,
        "features": [
            "5 Utilisateurs",
            "Gestion de stock avancée",
            "Ventes POS",
            "Rapports quotidiens",
            "Période d'essai de 14 jours"
        ],
        "trial_days": 14
    }
}


# ============================================================================
# FONCTIONS UTILITAIRES
# ============================================================================

def generate_subscription_code(length: int = 8) -> str:
    """
    Génère un code d'abonnement unique formaté XXXX-XXXX.
    """
    chars = string.ascii_uppercase + string.digits
    # Exclure les caractères ambigus
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    
    # Générer la première partie
    part1 = ''.join(random.choices(chars, k=4))
    part2 = ''.join(random.choices(chars, k=4))
    
    return f"{part1}-{part2}"


def get_plan_config(plan_type: str) -> Dict[str, Any]:
    """
    Récupère la configuration d'un plan.
    """
    return PLAN_CONFIG.get(plan_type, PLAN_CONFIG["free"])


def calculate_end_date(start_date: datetime, duration_days: int) -> datetime:
    """
    Calcule la date de fin à partir de la date de début et de la durée.
    """
    return start_date + timedelta(days=duration_days)


def is_unlimited(value: Union[int, str]) -> bool:
    """
    Vérifie si une valeur représente "Illimité".
    """
    if isinstance(value, str):
        return value.lower() == "illimité"
    return value == 0


def format_unlimited(value: Union[int, str]) -> Union[int, str]:
    """
    Formate une valeur pour l'affichage (transforme 0 en "Illimité").
    """
    if isinstance(value, int) and value == 0:
        return "Illimité"
    if isinstance(value, str) and value.lower() == "illimité":
        return "Illimité"
    return value


# ============================================================================
# FONCTIONS POUR LES ABONNEMENTS UTILISATEUR
# ============================================================================

def check_user_subscription(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Vérifie le statut de l'abonnement d'un utilisateur.
    Utilise le modèle UserSubscription.
    """
    user = db.query(User).filter(User.id == UUID(user_id)).first()
    if not user:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "free",
            "status": "no_user",
            "message": "Utilisateur non trouvé"
        }

    # Utiliser UserSubscription (pas Subscription)
    subscription = db.query(UserSubscription).filter(
        UserSubscription.user_id == user.id
    ).first()

    if not subscription:
        return {
            "has_subscription": False,
            "is_active": False,
            "plan": "free",
            "status": "no_subscription",
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id) if user.tenant_id else None
        }

    now = datetime.utcnow()
    is_active = subscription.status == "active" and (
        subscription.end_date is None or subscription.end_date > now
    )

    days_remaining = 0
    if subscription.end_date and is_active:
        days_remaining = (subscription.end_date - now).days

    return {
        "has_subscription": True,
        "is_active": is_active,
        "plan": subscription.plan_type,
        "plan_name": subscription.plan_name,
        "status": subscription.status,
        "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
        "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
        "days_remaining": max(0, days_remaining),
        "trial_end_date": subscription.trial_end_date.isoformat() if subscription.trial_end_date else None,
        "auto_renew": subscription.auto_renew,
        "billing_cycle": subscription.billing_cycle,
        "price": float(subscription.price or 0),
        "currency": subscription.currency or "EUR",
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None
    }


def create_user_subscription(
    db: Session,
    user_id: UUID,
    tenant_id: Optional[UUID] = None,
    plan_type: str = "trial"
) -> UserSubscription:
    """
    Crée un abonnement pour un utilisateur.
    """
    plan_config = get_plan_config(plan_type)
    trial_days = plan_config.get("trial_days", 14)
    
    now = datetime.utcnow()
    
    if plan_type == "trial":
        end_date = now + timedelta(days=trial_days)
        trial_end_date = end_date
    else:
        end_date = now + timedelta(days=30)  # 30 jours par défaut
        trial_end_date = None

    subscription = UserSubscription(
        user_id=user_id,
        tenant_id=tenant_id,
        plan_type=plan_type,
        plan_name=plan_config["name"],
        status="active",
        start_date=now,
        end_date=end_date,
        trial_end_date=trial_end_date,
        price=plan_config.get("price_monthly", 0),
        currency="EUR",
        billing_cycle="monthly",
        auto_renew=True,
        max_users=plan_config.get("max_users_per_tenant", 1),
        max_products=plan_config.get("max_products", 100),
        max_pharmacies=plan_config.get("max_pharmacies", 1),
        config={
            "created_at": now.isoformat(),
            "plan_config": plan_config
        }
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement utilisateur créé pour l'utilisateur {user_id} avec le plan {plan_type}")
    return subscription


def upgrade_user_subscription(
    db: Session,
    user_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None,
    payment_method: Optional[str] = None
) -> UserSubscription:
    """
    Met à niveau l'abonnement d'un utilisateur.
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"Utilisateur {user_id} non trouvé")

    plan_config = get_plan_config(new_plan)
    
    # Calculer le prix selon le cycle
    price_key = f"price_{billing_cycle}"
    price = plan_config.get(price_key, 0)

    now = datetime.utcnow()
    
    # Calculer la date de fin
    if billing_cycle == "yearly":
        end_date = now + timedelta(days=365)
    else:
        end_date = now + timedelta(days=30)

    # Vérifier si l'utilisateur a déjà un abonnement
    existing = db.query(UserSubscription).filter(
        UserSubscription.user_id == user_id
    ).first()

    if existing:
        # Mettre à jour l'abonnement existant
        existing.plan_type = new_plan
        existing.plan_name = plan_config["name"]
        existing.status = "active"
        existing.start_date = now
        existing.end_date = end_date
        existing.price = price
        existing.billing_cycle = billing_cycle
        existing.trial_end_date = None
        existing.max_users = plan_config.get("max_users_per_tenant", 1)
        existing.max_products = plan_config.get("max_products", 100)
        existing.max_pharmacies = plan_config.get("max_pharmacies", 1)
        
        # Mettre à jour la config
        config = existing.config or {}
        config.update({
            "upgraded_at": now.isoformat(),
            "previous_plan": existing.plan_type,
            "payment_id": payment_id,
            "payment_method": payment_method
        })
        existing.config = config
        
        subscription = existing
    else:
        # Créer un nouvel abonnement
        subscription = UserSubscription(
            user_id=user_id,
            tenant_id=user.tenant_id,
            plan_type=new_plan,
            plan_name=plan_config["name"],
            status="active",
            start_date=now,
            end_date=end_date,
            price=price,
            currency="EUR",
            billing_cycle=billing_cycle,
            auto_renew=True,
            max_users=plan_config.get("max_users_per_tenant", 1),
            max_products=plan_config.get("max_products", 100),
            max_pharmacies=plan_config.get("max_pharmacies", 1),
            config={
                "created_at": now.isoformat(),
                "payment_id": payment_id,
                "payment_method": payment_method
            }
        )
        db.add(subscription)

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement utilisateur mis à niveau pour l'utilisateur {user_id} vers {new_plan}")
    return subscription


# ============================================================================
# FONCTIONS DE RENOUVELLEMENT ET CHANGEMENT DE PLAN (ALIAS)
# ============================================================================

def upgrade_subscription(
    db: Session,
    user_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: str = "monthly",
    payment_id: Optional[str] = None,
    payment_method: Optional[str] = None
) -> UserSubscription:
    """
    Alias pour upgrade_user_subscription - Met à niveau l'abonnement d'un utilisateur.
    Utilisé par subscriptions.py pour être compatible avec l'ancien code.
    """
    logger.info(f"Appel de upgrade_subscription (alias) pour l'utilisateur {user_id} vers {new_plan}")
    return upgrade_user_subscription(
        db=db,
        user_id=user_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        payment_id=payment_id,
        payment_method=payment_method
    )


def renew_subscription(
    db: Session,
    user_id: Union[str, UUID],
    billing_cycle: Optional[str] = None
) -> UserSubscription:
    """
    Renouvelle l'abonnement d'un utilisateur pour une nouvelle période.
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"Utilisateur {user_id} non trouvé")

    if not user.subscription:
        raise ValueError("L'utilisateur n'a pas d'abonnement à renouveler")

    subscription = user.subscription
    now = datetime.utcnow()
    
    # Déterminer la période de renouvellement
    cycle = billing_cycle or subscription.billing_cycle
    
    # Calculer la nouvelle date de fin
    if cycle == "yearly":
        end_date = now + timedelta(days=365)
    elif cycle == "monthly":
        end_date = now + timedelta(days=30)
    else:
        end_date = now + timedelta(days=30)  # Par défaut

    # Mettre à jour l'abonnement
    subscription.start_date = now
    subscription.end_date = end_date
    subscription.status = "active"
    subscription.auto_renew = True
    
    # Mettre à jour la config
    config = subscription.config or {}
    config["renewed_at"] = now.isoformat()
    config["previous_end_date"] = subscription.end_date.isoformat() if subscription.end_date else None
    subscription.config = config

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement renouvelé pour l'utilisateur {user_id} jusqu'au {end_date}")
    return subscription


def change_subscription_plan(
    db: Session,
    user_id: Union[str, UUID],
    new_plan: str,
    billing_cycle: Optional[str] = None,
    immediate: bool = True
) -> UserSubscription:
    """
    Change le plan d'abonnement d'un utilisateur (sans paiement, pour les tests/administration).
    """
    if isinstance(user_id, str):
        user_id = UUID(user_id)

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"Utilisateur {user_id} non trouvé")

    plan_config = get_plan_config(new_plan)
    
    if not user.subscription:
        # Créer un nouvel abonnement
        return create_user_subscription(
            db=db,
            user_id=user_id,
            tenant_id=user.tenant_id,
            plan_type=new_plan
        )

    subscription = user.subscription
    now = datetime.utcnow()
    
    # Mettre à jour le plan
    subscription.plan_type = new_plan
    subscription.plan_name = plan_config["name"]
    subscription.price = plan_config.get("price_monthly", 0)
    
    # Mettre à jour les limites
    subscription.max_users = plan_config.get("max_users_per_tenant", 1)
    subscription.max_products = plan_config.get("max_products", 100)
    subscription.max_pharmacies = plan_config.get("max_pharmacies", 1)
    
    # Changer la date de fin si immédiat
    if immediate and billing_cycle:
        if billing_cycle == "yearly":
            subscription.end_date = now + timedelta(days=365)
        elif billing_cycle == "monthly":
            subscription.end_date = now + timedelta(days=30)
        subscription.billing_cycle = billing_cycle
    
    # Mettre à jour la config
    config = subscription.config or {}
    config["plan_changed_at"] = now.isoformat()
    config["previous_plan"] = subscription.plan_type
    config["new_plan"] = new_plan
    subscription.config = config

    db.commit()
    db.refresh(subscription)

    logger.info(f"Plan changé pour l'utilisateur {user_id} vers {new_plan}")
    return subscription


# ============================================================================
# FONCTION DE CRÉATION D'ESSAI
# ============================================================================

def create_trial_subscription(
    db: Session,
    user_id: UUID,
    tenant_id: Optional[UUID] = None
) -> UserSubscription:
    """
    Crée un abonnement d'essai pour un nouvel utilisateur.
    Alias pour create_user_subscription avec plan_type="trial".
    """
    logger.info(f"Création d'un abonnement d'essai pour l'utilisateur {user_id}")
    return create_user_subscription(
        db=db,
        user_id=user_id,
        tenant_id=tenant_id,
        plan_type="trial"
    )


# ============================================================================
# FONCTIONS POUR LES ABONNEMENTS TENANT
# ============================================================================

def get_tenant_subscription(db: Session, tenant_id: str) -> Optional[TenantSubscription]:
    """
    Récupère l'abonnement d'un tenant.
    """
    tenant = db.query(Tenant).filter(Tenant.id == UUID(tenant_id)).first()
    if not tenant:
        return None
    
    # Chercher l'admin du tenant pour avoir l'abonnement
    admin = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin"
    ).first()
    
    if admin and admin.subscription:
        # Si l'admin a un UserSubscription, l'utiliser
        return None  # Ceci est un UserSubscription, pas un TenantSubscription
    
    # Sinon, chercher un TenantSubscription
    subscription = db.query(TenantSubscription).filter(
        TenantSubscription.tenant_id == tenant.id
    ).first()
    
    return subscription


def create_tenant_subscription(
    db: Session,
    tenant_id: UUID,
    plan_type: str = "starter",
    created_by: Optional[UUID] = None
) -> TenantSubscription:
    """
    Crée un abonnement pour un tenant.
    """
    plan_config = get_plan_config(plan_type)
    
    now = datetime.utcnow()
    end_date = now + timedelta(days=30)  # 30 jours par défaut

    # Générer un code d'abonnement unique
    subscription_code = f"TEN-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"

    subscription = TenantSubscription(
        tenant_id=tenant_id,
        subscription_code=subscription_code,
        plan=plan_type,  # Note: TenantSubscription utilise 'plan' pas 'plan_type'
        plan_name=plan_config["name"],
        billing_period="mensuel",
        status="active",
        monthly_price=plan_config.get("price_monthly", 0),
        annual_price=plan_config.get("price_yearly", 0),
        current_price=plan_config.get("price_monthly", 0),
        start_date=now,
        end_date=end_date,
        max_users=plan_config.get("max_users_per_tenant", 1),
        max_products=plan_config.get("max_products", 100),
        auto_renew=True,
        created_by=created_by,
        features=str(plan_config.get("features", []))  # Convertir en string pour le stockage
    )

    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement tenant créé pour le tenant {tenant_id} avec le plan {plan_type}")
    return subscription


# ============================================================================
# FONCTIONS DE VÉRIFICATION DES LIMITES
# ============================================================================

def check_tenant_limits(db: Session, tenant_id: str) -> Dict[str, Any]:
    """
    Vérifie les limites d'un tenant par rapport à son abonnement.
    """
    from app.models.product import Product
    from app.models.pharmacy import Pharmacy

    tenant = db.query(Tenant).filter(Tenant.id == UUID(tenant_id)).first()
    if not tenant:
        return {"error": "Tenant non trouvé"}

    # Chercher d'abord un UserSubscription pour l'admin
    admin = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin"
    ).first()

    subscription = None
    plan_type = "free"
    plan_config = PLAN_CONFIG["free"]

    if admin and admin.subscription:
        # Utiliser l'abonnement de l'admin (UserSubscription)
        subscription = admin.subscription
        plan_type = subscription.plan_type
        plan_config = get_plan_config(plan_type)
    else:
        # Sinon, chercher un TenantSubscription
        tenant_sub = db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == tenant.id
        ).first()
        if tenant_sub:
            plan_type = tenant_sub.plan.value if hasattr(tenant_sub.plan, 'value') else str(tenant_sub.plan)
            plan_config = get_plan_config(plan_type)

    # Compter les utilisateurs actifs
    users_count = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.actif.is_(True)
    ).count()

    # Compter les produits
    products_count = db.query(Product).filter(
        Product.tenant_id == tenant.id
    ).count()

    # Compter les pharmacies
    pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id
    ).count()

    max_users = plan_config.get("max_users_per_tenant", 1)
    max_products = plan_config.get("max_products", 100)
    max_pharmacies = plan_config.get("max_pharmacies", 1)

    return {
        "has_subscription": subscription is not None,
        "plan": plan_type,
        "plan_name": plan_config["name"],
        "current": {
            "users": users_count,
            "products": products_count,
            "pharmacies": pharmacies_count
        },
        "limits": {
            "users": format_unlimited(max_users),
            "products": format_unlimited(max_products),
            "pharmacies": format_unlimited(max_pharmacies)
        },
        "percentages": {
            "users": (users_count / max_users * 100) if max_users > 0 else 0,
            "products": (products_count / max_products * 100) if max_products > 0 else 0,
            "pharmacies": (pharmacies_count / max_pharmacies * 100) if max_pharmacies > 0 else 0
        },
        "exceeded": {
            "users": users_count > max_users if max_users > 0 else False,
            "products": products_count > max_products if max_products > 0 else False,
            "pharmacies": pharmacies_count > max_pharmacies if max_pharmacies > 0 else False
        }
    }


def can_user_access_feature(user: User, feature: str) -> bool:
    """
    Vérifie si un utilisateur peut accéder à une fonctionnalité.
    """
    if not user.subscription:
        return False

    plan_config = get_plan_config(user.subscription.plan_type)
    features = plan_config.get("features", [])
    
    # Vérifier si la fonctionnalité est dans la liste
    return any(feature.lower() in f.lower() for f in features)


# ============================================================================
# GESTION DES CODES D'ABONNEMENT
# ============================================================================

def create_subscription_code(
    db: Session,
    created_by_user_id: UUID,
    plan_type: str,
    billing_cycle: str = "monthly",
    duration_days: Optional[int] = None,
    price: Optional[float] = None,
    currency: str = "EUR",
    valid_until: Optional[datetime] = None,
    notes: Optional[str] = None
) -> SubscriptionCode:
    """
    Crée un code d'abonnement (pour paiement cash).
    """
    plan_config = get_plan_config(plan_type)
    
    # Déterminer la durée
    if not duration_days:
        duration_days = 365 if billing_cycle == "yearly" else 30
    
    # Déterminer le prix
    if price is None:
        price_key = f"price_{billing_cycle}"
        price = plan_config.get(price_key, 0)
    
    # Déterminer la date de validité du code
    if not valid_until:
        valid_until = datetime.utcnow() + timedelta(days=90)  # 90 jours par défaut

    # Générer un code unique
    attempts = 0
    max_attempts = 10
    while attempts < max_attempts:
        code = generate_subscription_code()
        
        # Vérifier si le code existe déjà
        existing = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == code
        ).first()
        
        if not existing:
            break
        attempts += 1
    else:
        raise Exception("Impossible de générer un code unique après plusieurs tentatives")

    subscription_code = SubscriptionCode(
        code=code,
        plan_type=plan_type,
        plan_name=plan_config["name"],
        duration_days=duration_days,
        price=price,
        currency=currency,
        valid_from=datetime.utcnow(),
        valid_until=valid_until,
        status=SubscriptionCodeStatus.PENDING,
        created_by_user_id=created_by_user_id,
        notes=notes
    )

    db.add(subscription_code)
    db.commit()
    db.refresh(subscription_code)

    logger.info(f"Code d'abonnement généré: {code} pour le plan {plan_type}")
    return subscription_code


def validate_subscription_code(db: Session, code: str) -> Optional[SubscriptionCode]:
    """
    Valide un code d'abonnement.
    """
    # Nettoyer le code
    clean_code = code.strip().upper()
    
    subscription_code = db.query(SubscriptionCode).filter(
        SubscriptionCode.code == clean_code
    ).first()

    if not subscription_code:
        return None

    now = datetime.utcnow()
    
    # Vérifier la validité
    if subscription_code.status != SubscriptionCodeStatus.PENDING:
        return None
    
    if subscription_code.valid_from and now < subscription_code.valid_from:
        return None
    
    if subscription_code.valid_until and now > subscription_code.valid_until:
        subscription_code.status = SubscriptionCodeStatus.EXPIRED
        db.commit()
        return None

    return subscription_code


def activate_subscription_with_code(
    db: Session,
    user: User,
    code: SubscriptionCode
) -> UserSubscription:
    """
    Active un abonnement utilisateur avec un code.
    """
    now = datetime.utcnow()
    end_date = now + timedelta(days=code.duration_days)

    # Vérifier si l'utilisateur a déjà un abonnement
    existing = db.query(UserSubscription).filter(
        UserSubscription.user_id == user.id
    ).first()

    if existing:
        # Mettre à jour l'abonnement existant
        existing.plan_type = code.plan_type
        existing.plan_name = code.plan_name
        existing.status = "active"
        existing.start_date = now
        existing.end_date = end_date
        existing.price = code.price
        existing.currency = code.currency
        existing.billing_cycle = "code_activation"
        
        config = existing.config or {}
        config.update({
            "activated_with_code": code.code,
            "code_id": str(code.id),
            "activated_at": now.isoformat()
        })
        existing.config = config
        
        subscription = existing
    else:
        # Créer un nouvel abonnement
        plan_config = get_plan_config(code.plan_type)
        subscription = UserSubscription(
            user_id=user.id,
            tenant_id=user.tenant_id,
            plan_type=code.plan_type,
            plan_name=code.plan_name,
            status="active",
            start_date=now,
            end_date=end_date,
            price=code.price,
            currency=code.currency,
            billing_cycle="code_activation",
            auto_renew=False,
            max_users=plan_config.get("max_users_per_tenant", 1),
            max_products=plan_config.get("max_products", 100),
            max_pharmacies=plan_config.get("max_pharmacies", 1),
            config={
                "activated_with_code": code.code,
                "code_id": str(code.id),
                "activated_at": now.isoformat()
            }
        )
        db.add(subscription)

    # Marquer le code comme utilisé
    code.status = SubscriptionCodeStatus.ACTIVATED
    code.activated_by_user_id = user.id
    code.activated_at = now

    db.commit()
    db.refresh(subscription)

    logger.info(f"Abonnement activé avec code {code.code} pour l'utilisateur {user.id}")
    return subscription


# ============================================================================
# STATISTIQUES ET RAPPORTS
# ============================================================================

def get_user_subscription_usage(db: Session, user_id: str) -> Dict[str, Any]:
    """
    Récupère les statistiques d'utilisation de l'abonnement d'un utilisateur.
    """
    from app.models.product import Product
    from app.models.pharmacy import Pharmacy

    user = db.query(User).filter(User.id == UUID(user_id)).first()
    if not user:
        return {"error": "Utilisateur non trouvé"}

    if not user.tenant_id:
        return {
            "current_products": 0,
            "max_products": 100,
            "usage_percentage": 0,
            "remaining_products": 100,
            "current_users": 1,
            "max_users": 1,
            "users_usage_percentage": 0,
            "remaining_users": 0,
            "current_pharmacies": 0,
            "max_pharmacies": 1,
            "pharmacies_usage_percentage": 0,
            "remaining_pharmacies": 1
        }

    # Compter les produits
    products_count = db.query(Product).filter(
        Product.tenant_id == user.tenant_id
    ).count()

    # Compter les utilisateurs actifs
    users_count = db.query(User).filter(
        User.tenant_id == user.tenant_id,
        User.actif.is_(True)
    ).count()

    # Compter les pharmacies
    pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == user.tenant_id
    ).count()

    # Récupérer les limites du plan de l'utilisateur
    if user.subscription:
        plan_config = get_plan_config(user.subscription.plan_type)
        max_products = plan_config.get("max_products", 100)
        max_users = plan_config.get("max_users_per_tenant", 1)
        max_pharmacies = plan_config.get("max_pharmacies", 1)
    else:
        max_products = 100
        max_users = 1
        max_pharmacies = 1

    # Calculer les pourcentages
    products_percentage = (products_count / max_products * 100) if max_products > 0 else 0
    users_percentage = (users_count / max_users * 100) if max_users > 0 else 0
    pharmacies_percentage = (pharmacies_count / max_pharmacies * 100) if max_pharmacies > 0 else 0

    return {
        "current_products": products_count,
        "max_products": format_unlimited(max_products),
        "usage_percentage": round(min(100, products_percentage), 2),
        "remaining_products": format_unlimited(max(0, max_products - products_count) if max_products > 0 else "Illimité"),
        
        "current_users": users_count,
        "max_users": format_unlimited(max_users),
        "users_usage_percentage": round(min(100, users_percentage), 2),
        "remaining_users": format_unlimited(max(0, max_users - users_count) if max_users > 0 else "Illimité"),
        
        "current_pharmacies": pharmacies_count,
        "max_pharmacies": format_unlimited(max_pharmacies),
        "pharmacies_usage_percentage": round(min(100, pharmacies_percentage), 2),
        "remaining_pharmacies": format_unlimited(max(0, max_pharmacies - pharmacies_count) if max_pharmacies > 0 else "Illimité"),
        
        "subscription": {
            "plan_name": user.subscription.plan_name if user.subscription else "Gratuit",
            "plan_type": user.subscription.plan_type if user.subscription else "free",
            "status": user.subscription.status if user.subscription else "inactive",
            "price": float(user.subscription.price or 0) if user.subscription else 0,
            "currency": user.subscription.currency or "EUR" if user.subscription else "EUR",
            "billing_cycle": user.subscription.billing_cycle if user.subscription else None,
            "current_period_start": user.subscription.start_date.isoformat() if user.subscription and user.subscription.start_date else None,
            "current_period_end": user.subscription.end_date.isoformat() if user.subscription and user.subscription.end_date else None,
        } if user.subscription else None
    }


def get_available_plans(include_trial: bool = False) -> List[Dict[str, Any]]:
    """
    Récupère la liste des plans disponibles.
    """
    plans = []
    
    for key, config in PLAN_CONFIG.items():
        if key == "trial" and not include_trial:
            continue
            
        plans.append({
            "id": key,
            "name": config["name"],
            "type": key,
            "price": config["price_monthly"],
            "price_monthly": config["price_monthly"],
            "price_yearly": config["price_yearly"],
            "max_users": format_unlimited(config.get("max_users_per_tenant", 0)),
            "max_products": format_unlimited(config.get("max_products", 0)),
            "max_pharmacies": format_unlimited(config.get("max_pharmacies", 0)),
            "features": config.get("features", []),
            "is_popular": key == "pro",
            "description": f"Plan {config['name']}",
            "billing_cycle": "monthly"
        })
    
    return plans


def get_subscription_summary_for_superadmin(
    db: Session,
    tenant_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Récupère un résumé des abonnements pour le super admin.
    Inclut à la fois les abonnements tenant et utilisateur.
    """
    # Abonnements tenant
    tenant_query = db.query(TenantSubscription)
    if tenant_id:
        tenant_query = tenant_query.filter(TenantSubscription.tenant_id == UUID(tenant_id))
    tenant_subs = tenant_query.all()
    
    # Abonnements utilisateur
    user_query = db.query(UserSubscription)
    if tenant_id:
        # Filtrer par tenant_id si spécifié
        user_query = user_query.filter(UserSubscription.tenant_id == UUID(tenant_id))
    user_subs = user_query.all()
    
    total_revenue = 0
    for sub in tenant_subs:
        if sub.status == "active" or (hasattr(sub.status, 'value') and sub.status.value == "active"):
            total_revenue += float(sub.current_price or 0)
    
    for sub in user_subs:
        if sub.status == "active":
            total_revenue += float(sub.price or 0)
    
    return {
        "tenant_subscriptions": len(tenant_subs),
        "user_subscriptions": len(user_subs),
        "total_subscriptions": len(tenant_subs) + len(user_subs),
        "total_monthly_revenue": float(total_revenue),
        "projected_yearly_revenue": float(total_revenue * 12),
        "tenant_subscriptions_list": [
            {
                "id": str(sub.id),
                "tenant_id": str(sub.tenant_id),
                "plan": sub.plan.value if hasattr(sub.plan, 'value') else str(sub.plan),
                "plan_name": sub.plan_name,
                "status": sub.status.value if hasattr(sub.status, 'value') else str(sub.status),
                "start_date": sub.start_date.isoformat() if sub.start_date else None,
                "end_date": sub.end_date.isoformat() if sub.end_date else None,
                "price": float(sub.current_price or 0),
                "type": "tenant"
            }
            for sub in tenant_subs
        ],
        "user_subscriptions_list": [
            {
                "id": str(sub.id),
                "user_id": str(sub.user_id),
                "tenant_id": str(sub.tenant_id) if sub.tenant_id else None,
                "plan": sub.plan_type,
                "plan_name": sub.plan_name,
                "status": sub.status,
                "start_date": sub.start_date.isoformat() if sub.start_date else None,
                "end_date": sub.end_date.isoformat() if sub.end_date else None,
                "price": float(sub.price or 0),
                "type": "user"
            }
            for sub in user_subs
        ]
    }


def create_subscription_payment(
    db: Session,
    user_id: str,
    payment_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Enregistre un paiement d'abonnement pour un utilisateur.
    """
    user = db.query(User).filter(User.id == UUID(user_id)).first()
    if not user:
        raise ValueError("Utilisateur non trouvé")

    plan = payment_data.get("plan")
    billing_period = payment_data.get("billing_period", "monthly")
    payment_method = payment_data.get("payment_method")
    amount = payment_data.get("amount")
    reference = payment_data.get("reference")

    # Mettre à jour l'abonnement utilisateur
    subscription = upgrade_subscription(
        db=db,
        user_id=user.id,
        new_plan=plan,
        billing_cycle=billing_period,
        payment_id=reference,
        payment_method=payment_method
    )

    return {
        "success": True,
        "subscription_id": str(subscription.id),
        "reference": reference,
        "amount": float(amount),
        "currency": "EUR",
        "payment_method": payment_method,
        "paid_at": datetime.utcnow().isoformat(),
        "type": "user_subscription"
    }


# Aliases pour compatibilité avec l'ancien code
check_subscription_status = check_user_subscription
get_subscription_usage = get_user_subscription_usage