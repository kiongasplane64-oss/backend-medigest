# app/models/invoice.py
import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, ForeignKey, Float, Boolean, Text, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base

class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"

class InvoiceType(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    ONE_TIME = "one_time"
    RENEWAL = "renewal"

class Invoice(Base):
    __tablename__ = "invoices"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    # Numéro de facture unique
    invoice_number = Column(String(50), unique=True, nullable=False)
    invoice_type = Column(SQLEnum(InvoiceType), nullable=False, default=InvoiceType.SUBSCRIPTION)
    
    # Période facturée
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=False)
    
    # Montants
    subtotal = Column(Float, nullable=False, default=0)
    tax_rate = Column(Float, default=0)
    tax_amount = Column(Float, default=0)
    discount_amount = Column(Float, default=0)
    total_amount = Column(Float, nullable=False)
    
    # Devise
    currency = Column(String(3), nullable=False, default="EUR")
    
    # Statut
    status = Column(SQLEnum(InvoiceStatus), nullable=False, default=InvoiceStatus.DRAFT)
    
    # Dates
    issue_date = Column(DateTime, nullable=False, default=datetime.utcnow)
    due_date = Column(DateTime, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    
    # Métadonnées
    description = Column(Text, nullable=True)
    subscription_plan = Column(String(50), nullable=True)
    billing_cycle = Column(String(20), nullable=True)
    payment_method = Column(String(50), nullable=True)
    payment_reference = Column(String(100), nullable=True)
    
    # Configuration JSON pour flexibilité
    invoice_metadata = Column(JSON, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relations
    pharmacy = relationship("Pharmacy", foreign_keys=[pharmacy_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    
    def is_overdue(self) -> bool:
        return self.status != InvoiceStatus.PAID and self.due_date < datetime.utcnow()
    
    def days_overdue(self) -> int:
        if not self.is_overdue():
            return 0
        return (datetime.utcnow() - self.due_date).days