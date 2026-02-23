# app/models/tenant.py
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from enum import Enum

from sqlalchemy import (
    Column, String, DateTime, Boolean, Text,
    Integer, DECIMAL, ForeignKey, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, validates
from app.db.base import Base


# =========================
# ENUMS SAAS
# =========================
class TenantStatus(str, Enum):
    DRAFT = "draft"
    TRIAL = "trial"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"

class BillingPeriod(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUALLY = "annually"
    ONE_TIME = "one_time"


class PharmacyType(str, Enum):
    OFFICINE = "officine"
    HOSPITALIERE = "hospitaliere"
    VETERINAIRE = "veterinaire"
    PARAPHARMACIE = "parapharmacie"
    GROSSISTE = "grossiste"
    AUTRE = "autre"


class Tenant(Base):
    __tablename__ = "tenants"
    
    # =========================
    # IDENTITÉ
    # =========================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_code = Column(String(20), unique=True, nullable=False, index=True)
    slug = Column(String(100), unique=True, index=True)

    # =========================
    # INFOS GÉNÉRALES
    # =========================
    nom_pharmacie = Column(String(200), nullable=False)
    nom_commercial = Column(String(200))
    description = Column(Text)

    # =========================
    # CONTACT
    # =========================
    email_admin = Column(String(150), nullable=False, index=True)
    telephone_principal = Column(String(20), nullable=False)
    adresse = Column(String(500), nullable=True, comment="Adresse complète de la pharmacie")
    ville = Column(String(100), nullable=False)
    province = Column(String(100))
    pays = Column(String(100), default="RDC")

    # =========================
    # RESPONSABLE
    # =========================
    nom_proprietaire = Column(String(150), nullable=False)

    # =========================
    # CARACTÉRISTIQUES
    # =========================
    type_pharmacie = Column(String(30), default=PharmacyType.OFFICINE)
    nombre_employes = Column(Integer, default=1)

    # =========================
    # ABONNEMENT SAAS
    # =========================
    status = Column(String(20), default=TenantStatus.DRAFT, index=True)
    current_plan = Column(String(50))
    subscription_start_date = Column(DateTime)
    subscription_end_date = Column(DateTime)
    trial_start_date = Column(DateTime)
    trial_end_date = Column(DateTime)

    auto_renew = Column(Boolean, default=True)
    monthly_rate = Column(DECIMAL(10, 2), default=0)

    # =========================
    # CONFIGURATION
    # =========================
    config = Column(JSONB, default=dict)
    meta_data = Column(JSONB, default=dict)
    tags = Column(JSONB, default=list)
    notes = Column(Text)

    # =========================
    # LIMITES SAAS
    # =========================
    max_users = Column(Integer, default=3)
    max_products = Column(Integer, default=1000)
    max_pharmacies = Column(Integer, default=1)

    # =========================
    # AUDIT
    # =========================
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    activated_at = Column(DateTime)
    suspended_at = Column(DateTime)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # =========================
    # RELATIONS
    # =========================
    users = relationship(
        "app.models.user.User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        foreign_keys="[app.models.user.User.tenant_id]"
    )

    creator = relationship(
        "app.models.user.User",
        foreign_keys=[created_by],
        lazy="joined",
        uselist=False
    )

        # Utilisez des chemins complets partout
    payments = relationship("Payment", back_populates="tenant", cascade="all, delete-orphan")
    
    # Toutes les autres relations aussi :
    costs = relationship("app.models.cost.Cost", back_populates="tenant", lazy="noload")
    budgets = relationship("app.models.cost.Budget", back_populates="tenant", lazy="noload")
    suppliers = relationship("app.models.cost.Supplier", back_populates="tenant", lazy="noload")
    subscriptions = relationship("app.models.subscription.Subscription", back_populates="tenant", lazy="noload")
    debt_payments = relationship("app.models.debt_payment.DebtPayment", back_populates="tenant")
    debts = relationship("app.models.debt.Debt", back_populates="tenant")
    sales = relationship("app.models.sale.Sale", back_populates="tenant")
    refunds = relationship("app.models.refund.Refund", back_populates="tenant")
    financial_transactions = relationship("FinancialTransaction", back_populates="tenant")
    financial_periods = relationship("FinancialPeriod", back_populates="tenant")
    capitals = relationship("Capital", back_populates="tenant")
    expenses = relationship("Expense", back_populates="tenant")
    clients = relationship("app.models.client.Client", back_populates="tenant")
    invoices = relationship("Invoice", back_populates="tenant", cascade="all, delete-orphan")
    products = relationship("app.models.product.Product", back_populates="tenant")
    invoice_payments = relationship("InvoicePayment", back_populates="tenant", cascade="all, delete-orphan")
        
    budgets = relationship(
    "app.models.cost.Budget",  # Utilisez le chemin string
    back_populates="tenant",
    lazy="noload"
    )

    suppliers = relationship(
        "app.models.cost.Supplier",  # Utilisez le chemin string
        back_populates="tenant",
        lazy="noload"
    )
    pharmacies = relationship("Pharmacy", back_populates="tenant", cascade="all, delete-orphan")


    # =========================
    # INDEXES
    # =========================
    __table_args__ = (
        Index("ix_tenants_status", "status"),
        Index("ix_tenants_plan", "current_plan"),
        Index("ix_tenants_ville", "ville"),
        {"extend_existing": True}
    )

    # =========================
    # VALIDATIONS
    # =========================
    @validates("email_admin")
    def validate_email(self, key, value):
        if "@" not in value:
            raise ValueError("Email administrateur invalide")
        return value.lower().strip()

    # =========================
    # PROPRIÉTÉS SAAS
    # =========================
    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE

    @property
    def is_trial(self) -> bool:
        return self.status == TenantStatus.TRIAL

    @property
    def subscription_expired(self) -> bool:
        if not self.subscription_end_date:
            return False
        return datetime.utcnow() > self.subscription_end_date

    @property
    def trial_days_remaining(self) -> Optional[int]:
        if not self.trial_end_date:
            return None
        remaining = (self.trial_end_date - datetime.utcnow()).days
        return max(remaining, 0)

    @property
    def active_pharmacies_count(self) -> int:
        """Retourne le nombre de pharmacies actives"""
        if not self.pharmacies:
            return 0
        return len([p for p in self.pharmacies if p.is_active])
    
    # =========================
    # ACTIONS SAAS
    # =========================
    def activate(self):
        self.status = TenantStatus.ACTIVE
        self.activated_at = datetime.utcnow()

    def suspend(self, reason: Optional[str] = None):
        self.status = TenantStatus.SUSPENDED
        self.suspended_at = datetime.utcnow()
        if reason:
            self.notes = f"{self.notes or ''}\n[SUSPENSION] {reason}"

    def start_trial(self, days: int = 14):
        self.status = TenantStatus.TRIAL
        self.trial_start_date = datetime.utcnow()
        self.trial_end_date = datetime.utcnow() + timedelta(days=days)

    # =========================
    # SERIALISATION API
    # =========================
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_code": self.tenant_code,
            "nom_pharmacie": self.nom_pharmacie,
            "ville": self.ville,
            "status": self.status,
            "current_plan": self.current_plan,
            "is_active": self.is_active,
            "is_trial": self.is_trial,
            "trial_days_remaining": self.trial_days_remaining,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "pharmacies_count": len(self.pharmacies) if self.pharmacies else 0,
            "active_pharmacies_count": self.active_pharmacies_count,
        }
    
    def __repr__(self):
        return f"<Tenant {self.tenant_code} ({self.status})>"
