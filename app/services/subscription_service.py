from datetime import datetime
from sqlalchemy.orm import Session

from app.models.subscription import Subscription, SubscriptionStatus
from app.models.tenant import Tenant
from app.models.payment import Payment


def is_subscription_active(db: Session, tenant_id):
    """
    Vérifie si l'abonnement est actif pour un tenant
    (fonction conservée pour compatibilité)
    """
    subscription = (
        db.query(Subscription)
        .filter(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.end_date.desc())
        .first()
    )

    if not subscription:
        return False

    now = datetime.utcnow()

    # Abonnement expiré
    if subscription.end_date and subscription.end_date < now:
        subscription.status = SubscriptionStatus.EXPIRED
        db.commit()
        return False

    return subscription.status == SubscriptionStatus.ACTIVE


def get_active_subscription(db: Session, tenant_id):
    """
    Récupère le dernier abonnement d’un tenant
    """
    return (
        db.query(Subscription)
        .filter(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.end_date.desc())
        .first()
    )


def check_subscription_status(db: Session, tenant_id: str) -> bool:
    """
    Vérifie le statut de l'abonnement
    - ACTIVE non expiré
    - ou TRIAL valide
    """
    subscription = get_active_subscription(db, tenant_id)

    if not subscription:
        return False

    now = datetime.utcnow()

    # ACTIVE
    if subscription.status == SubscriptionStatus.ACTIVE:
        return subscription.end_date and subscription.end_date > now

    # TRIAL
    if subscription.status == SubscriptionStatus.TRIAL:
        return (
            subscription.trial_end_date
            and subscription.trial_end_date > now
        )

    return False

def check_subscription_status(db: Session, tenant_id: str) -> bool:
    """Vérifie si l'abonnement est actif"""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return False
    
    # Si en période d'essai
    if tenant.status == "trial" and tenant.trial_end_date:
        if datetime.utcnow() <= tenant.trial_end_date:
            return True
        else:
            tenant.status = "expired"
            db.commit()
            return False
    
    # Vérifier le dernier paiement
    last_payment = db.query(Payment).filter(
        Payment.tenant_id == tenant_id,
        Payment.subscription_plan.isnot(None),
        Payment.status == "success"
    ).order_by(Payment.paid_at.desc()).first()
    
    if not last_payment or not last_payment.period_end:
        return False
    
    # Vérifier si la période est encore valide
    is_active = datetime.utcnow() <= last_payment.period_end
    
    # Mettre à jour le statut du tenant
    tenant.status = "active" if is_active else "expired"
    db.commit()
    
    return is_active
