# app/models/tenant.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, date
from enum import Enum
from typing import Optional, Dict, Any

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Boolean,
    Text,
    Integer,
    DECIMAL,
    ForeignKey,
    Index,
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
    slug = Column(String(100), unique=True, nullable=True, index=True)

    # =========================
    # INFOS GÉNÉRALES
    # =========================
    nom_pharmacie = Column(String(200), nullable=False)
    nom_commercial = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)

    # =========================
    # CONTACT
    # =========================
    email_admin = Column(String(150), nullable=False, index=True)
    telephone_principal = Column(String(20), nullable=False)
    adresse = Column(String(500), nullable=True, comment="Adresse complète de la pharmacie")
    ville = Column(String(100), nullable=False)
    province = Column(String(100), nullable=True)
    pays = Column(String(100), nullable=False, default="RDC")

    # =========================
    # RESPONSABLE
    # =========================
    nom_proprietaire = Column(String(150), nullable=False)

    # =========================
    # CARACTÉRISTIQUES
    # =========================
    # Stocke la valeur (string) de l'enum pour rester simple côté DB
    type_pharmacie = Column(String(30), nullable=False, default=PharmacyType.OFFICINE.value)
    nombre_employes = Column(Integer, nullable=False, default=1)

    # =========================
    # ABONNEMENT SAAS
    # =========================
    status = Column(String(20), nullable=False, default=TenantStatus.DRAFT.value, index=True)
    current_plan = Column(String(50), nullable=True, index=True)
    subscription_start_date = Column(DateTime, nullable=True)
    subscription_end_date = Column(DateTime, nullable=True)
    trial_start_date = Column(DateTime, nullable=True)
    trial_end_date = Column(DateTime, nullable=True)

    auto_renew = Column(Boolean, nullable=False, default=True)
    monthly_rate = Column(DECIMAL(10, 2), nullable=False, default=0)

    # =========================
    # CONFIGURATION
    # =========================
    config = Column(JSONB, nullable=False, default=dict)
    meta_data = Column(JSONB, nullable=False, default=dict)
    tags = Column(JSONB, nullable=False, default=list)
    notes = Column(Text, nullable=True)

    # =========================
    # LIMITES SAAS
    # =========================
    max_users = Column(Integer, nullable=False, default=3)
    max_products = Column(Integer, nullable=False, default=1000)
    max_pharmacies = Column(Integer, nullable=False, default=1)

    # =========================
    # AUDIT
    # =========================
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    activated_at = Column(DateTime, nullable=True)
    suspended_at = Column(DateTime, nullable=True)

    # IMPORTANT:
    # - nullable=True casse le cycle tenants<->users à la création.
    # - On va créer la contrainte FK via migration APRES (ou la laisser nullable).
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", use_alter=True, name="fk_tenants_created_by_users"), nullable=True, index=True)

    # =========================
    # RELATIONS
    # =========================
    # On utilise des chemins "courts" pour éviter des imports doubles et rester compatible Alembic
    users = relationship(
        "User",
        back_populates="tenant",
        cascade="all, delete-orphan",
        foreign_keys="User.tenant_id",
        lazy="selectin",
    )

    creator = relationship(
        "User",
        foreign_keys=[created_by],
        lazy="joined",
        uselist=False,
    )

    # Relations principales (chemins string simples, plus robustes)
    payments = relationship("Payment", back_populates="tenant", cascade="all, delete-orphan")
    costs = relationship("Cost", back_populates="tenant", lazy="noload")
    budgets = relationship("Budget", back_populates="tenant", lazy="noload")
    suppliers = relationship("Supplier", back_populates="tenant", lazy="noload")
    subscriptions = relationship("Subscription", back_populates="tenant", lazy="noload")

    debt_payments = relationship("DebtPayment", back_populates="tenant")
    debts = relationship("Debt", back_populates="tenant")

    sales = relationship("Sale", back_populates="tenant")
    refunds = relationship("Refund", back_populates="tenant")

    financial_transactions = relationship("FinancialTransaction", back_populates="tenant")
    financial_periods = relationship("FinancialPeriod", back_populates="tenant")
    capitals = relationship("Capital", back_populates="tenant")
    expenses = relationship("Expense", back_populates="tenant")

    clients = relationship("Client", back_populates="tenant")
    invoices = relationship("Invoice", back_populates="tenant", cascade="all, delete-orphan")
    invoice_payments = relationship("InvoicePayment", back_populates="tenant", cascade="all, delete-orphan")

    products = relationship("Product", back_populates="tenant")
    pharmacies = relationship("Pharmacy", back_populates="tenant", cascade="all, delete-orphan")

    # =========================
    # INDEXES
    # =========================
    __table_args__ = (
        Index("ix_tenants_status", "status"),
        Index("ix_tenants_plan", "current_plan"),
        Index("ix_tenants_ville", "ville"),
        Index("ix_tenants_slug", "slug"),
        Index("ix_tenants_tenant_code", "tenant_code"),
    )

    # =========================
    # VALIDATIONS
    # =========================
    @validates("email_admin")
    def validate_email(self, key, value: str) -> str:
        if not value or "@" not in value:
            raise ValueError("Email administrateur invalide")
        return value.lower().strip()

    # =========================
    # PROPRIÉTÉS SAAS
    # =========================
    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE.value

    @property
    def is_trial(self) -> bool:
        return self.status == TenantStatus.TRIAL.value

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
        if not self.pharmacies:
            return 0
        return len([p for p in self.pharmacies if getattr(p, "is_active", False)])

    # =========================
    # ACTIONS SAAS
    # =========================
    def activate(self) -> None:
        self.status = TenantStatus.ACTIVE.value
        self.activated_at = datetime.utcnow()

    def suspend(self, reason: Optional[str] = None) -> None:
        self.status = TenantStatus.SUSPENDED.value
        self.suspended_at = datetime.utcnow()
        if reason:
            self.notes = f"{self.notes or ''}\n[SUSPENSION] {reason}".strip()

    def start_trial(self, days: int = 14) -> None:
        self.status = TenantStatus.TRIAL.value
        self.trial_start_date = datetime.utcnow()
        self.trial_end_date = datetime.utcnow() + timedelta(days=days)

    # =========================
    # SERIALISATION API
    # =========================
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_code": self.tenant_code,
            "slug": self.slug,
            "nom_pharmacie": self.nom_pharmacie,
            "nom_commercial": self.nom_commercial,
            "email_admin": self.email_admin,
            "telephone_principal": self.telephone_principal,
            "adresse": self.adresse,
            "ville": self.ville,
            "province": self.province,
            "pays": self.pays,
            "nom_proprietaire": self.nom_proprietaire,
            "type_pharmacie": self.type_pharmacie,
            "nombre_employes": self.nombre_employes,
            "status": self.status,
            "current_plan": self.current_plan,
            "is_active": self.is_active,
            "is_trial": self.is_trial,
            "trial_days_remaining": self.trial_days_remaining,
            "created_by": str(self.created_by) if self.created_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "pharmacies_count": len(self.pharmacies) if self.pharmacies else 0,
            "active_pharmacies_count": self.active_pharmacies_count,
        }

    def __repr__(self) -> str:
        return f"<Tenant {self.tenant_code} ({self.status})>"