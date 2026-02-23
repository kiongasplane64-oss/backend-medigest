# app/models/client.py
import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    Index,
    DECIMAL,
    ForeignKeyConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"

    # =======================
    # Identité
    # =======================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    nom = Column(String(100), nullable=False)
    telephone = Column(String(20))
    email = Column(String(100))
    adresse = Column(String(200))

    type_client = Column(
        String(20),
        default="particulier",
        comment="particulier, professionnel, assureur, etat",
    )

    # =======================
    # Informations légales
    # =======================
    entreprise = Column(String(100))
    num_contribuable = Column(String(50))
    rccm = Column(String(50))
    id_nat = Column(String(50))

    # =======================
    # Crédit & statistiques
    # =======================
    credit_limit = Column(DECIMAL(15, 2), default=0)
    eligible_credit = Column(Boolean, default=False)
    dette_actuelle = Column(DECIMAL(15, 2), default=0)
    credit_score = Column(Integer, default=100)

    total_achats = Column(DECIMAL(15, 2), default=0)
    nombre_achats = Column(Integer, default=0)
    moyenne_achat = Column(DECIMAL(15, 2), default=0)

    # =======================
    # Suivi
    # =======================
    date_inscription = Column(DateTime, default=datetime.utcnow)
    dernier_achat = Column(DateTime)
    date_dernier_paiement = Column(DateTime)

    notes = Column(Text)
    preferences = Column(JSON, default=dict)

    # ⚠️ éviter conflit SQLAlchemy
    client_metadata = Column("metadata", JSON, default=dict)

    is_active = Column(Boolean, default=True)
    blacklisted = Column(Boolean, default=False)
    blacklist_reason = Column(Text)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant", back_populates="clients")

    sales = relationship(
        "Sale",
        back_populates="client",
        cascade="all, delete-orphan",
        foreign_keys="Sale.client_id"
    )

    debts = relationship(
        "Debt",
        back_populates="client",
        cascade="all, delete-orphan",
        foreign_keys="Debt.client_id"
    )

    debt_payments = relationship(
        "DebtPayment",
        back_populates="client",
        cascade="all, delete-orphan",
        foreign_keys="DebtPayment.client_id"
    )

    # =======================
    # Indexes
    # =======================
    __table_args__ = (
        Index("ix_clients_tenant_nom", "tenant_id", "nom"),
        Index("ix_clients_tenant_phone", "tenant_id", "telephone"),
        Index("ix_clients_tenant_email", "tenant_id", "email"),
        Index("ix_clients_type", "tenant_id", "type_client"),
        Index("ix_clients_credit_status", "tenant_id", "eligible_credit", "blacklisted"),
        Index("ix_clients_last_purchase", "tenant_id", "dernier_achat"),
    )

    # =======================
    # Propriétés métier
    # =======================
    @property
    def credit_available(self):
        return max(0, float(self.credit_limit - self.dette_actuelle))

    @property
    def days_since_last_purchase(self):
        if self.dernier_achat:
            return (datetime.utcnow() - self.dernier_achat).days
        return None

    # =======================
    # Méthodes métier
    # =======================
    def update_stats(self, sale_amount):
        self.total_achats += sale_amount
        self.nombre_achats += 1
        self.moyenne_achat = self.total_achats / self.nombre_achats
        self.dernier_achat = datetime.utcnow()
        return self

    def add_debt(self, amount):
        self.dette_actuelle += amount
        if self.dette_actuelle > self.credit_limit:
            self.eligible_credit = False
        return self

    def pay_debt(self, amount):
        self.dette_actuelle = max(0, self.dette_actuelle - amount)
        self.date_dernier_paiement = datetime.utcnow()
        if self.dette_actuelle <= self.credit_limit * 0.8:
            self.eligible_credit = True
        return self

    def __repr__(self):
        return f"<Client {self.nom} ({self.telephone})>"