# app/services/invoice_service.py
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from uuid import uuid4

from app.models.invoice import Invoice, InvoiceStatus, InvoiceType
from app.models.pharmacy import Pharmacy
from app.models.pharmacy_subscription import PharmacySubscription

def generate_invoice_number(pharmacy_code: str, year: int, month: int, sequence: int) -> str:
    """Génère un numéro de facture unique."""
    return f"INV-{pharmacy_code}-{year}{month:02d}-{sequence:04d}"

def create_subscription_invoice(
    db: Session,
    pharmacy: Pharmacy,
    subscription: PharmacySubscription,
    billing_cycle: str = "monthly"
) -> Invoice:
    """Crée une facture pour un abonnement."""
    from app.services.pharmacy_subscription_service import get_plan_limits
    
    now = datetime.utcnow()
    plan_limits = get_plan_limits(subscription.plan)
    
    # Calculer la période
    if billing_cycle == "yearly":
        period_start = now
        period_end = now + timedelta(days=365)
        price = plan_limits.get("yearly_price", 0)
    else:
        period_start = now
        period_end = now + timedelta(days=30)
        price = plan_limits.get("monthly_price", 0)
    
    # Compter les factures existantes pour le numéro
    year = now.year
    month = now.month
    count = db.query(Invoice).filter(
        Invoice.invoice_number.like(f"INV-{pharmacy.pharmacy_code}-{year}{month:02d}%")
    ).count()
    
    invoice_number = generate_invoice_number(
        pharmacy.pharmacy_code or pharmacy.id.hex[:6].upper(),
        year, month, count + 1
    )
    
    invoice = Invoice(
        id=uuid4(),
        pharmacy_id=pharmacy.id,
        tenant_id=pharmacy.tenant_id,
        invoice_number=invoice_number,
        invoice_type=InvoiceType.SUBSCRIPTION,
        period_start=period_start,
        period_end=period_end,
        subtotal=price,
        tax_rate=16.0,  # TVA par défaut
        tax_amount=price * 0.16,
        discount_amount=0,
        total_amount=price * 1.16,
        currency="EUR",
        status=InvoiceStatus.SENT,
        issue_date=now,
        due_date=now + timedelta(days=15),
        description=f"Abonnement {plan_limits['name']} - {billing_cycle}",
        subscription_plan=subscription.plan,
        billing_cycle=billing_cycle,
        metadata={
            "plan_name": plan_limits["name"],
            "max_products": subscription.max_products,
            "max_users": subscription.max_users,
            "billing_cycle": billing_cycle
        }
    )
    
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    
    return invoice

def get_pharmacy_invoices(
    db: Session,
    pharmacy_id: str,
    limit: int = 50,
    offset: int = 0,
    start_date: datetime = None,
    end_date: datetime = None
) -> tuple[list[Invoice], int]:
    """Récupère les factures d'une pharmacie."""
    query = db.query(Invoice).filter(Invoice.pharmacy_id == pharmacy_id)
    
    if start_date:
        query = query.filter(Invoice.issue_date >= start_date)
    if end_date:
        query = query.filter(Invoice.issue_date <= end_date)
    
    total = query.count()
    invoices = query.order_by(Invoice.issue_date.desc()).offset(offset).limit(limit).all()
    
    return invoices, total