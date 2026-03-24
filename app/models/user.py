# app/models/user.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base

class User(Base):
    __tablename__ = "users"

    # =========================
    # Identité & Auth
    # =========================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    nom_complet = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(50), default="pharmacien", nullable=False)
    actif = Column(Boolean, default=True)
    telephone = Column(String(20), nullable=True)
    adresse = Column(String(200), nullable=True)
    permissions = Column(JSON, nullable=True)

    # =========================
    # Sécurité & Contrôle accès
    # =========================
    sms_code = Column(String(10), nullable=True)
    sms_expires_at = Column(DateTime, nullable=True)
    sms_verify_attempts = Column(Integer, default=0)
    
    reset_code = Column(String(10), nullable=True)
    reset_expires = Column(DateTime, nullable=True)

    login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)

    # =========================
    # Dates
    # =========================
    date_creation = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    date_modification = Column(
        DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow
    )

    # =========================
    # Relations (Corrigées avec Overlaps)
    # =========================
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])
    
    # Relation Many-to-Many directe vers les pharmacies
    pharmacies = relationship(
        "Pharmacy",
        secondary="user_pharmacy",
        back_populates="users",
        overlaps="pharmacy_associations,user_associations,user,pharmacy"
    )

    # Relation vers la table d'association (pour les champs is_primary, can_manage)
    pharmacy_associations = relationship(
        "UserPharmacy",
        back_populates="user",
        cascade="all, delete-orphan",
        overlaps="pharmacies,users"
    )

    # Relations inverses pour les modules tiers (Cost, Debt, etc.)
    # Utilisation de overlaps pour corriger les erreurs de log
    tenants_created = relationship("Tenant", back_populates="creator", foreign_keys="[Tenant.created_by]", lazy="noload")
    
    costs_created = relationship("Cost", foreign_keys="Cost.created_by", back_populates="creator", lazy="noload")
    costs_approved = relationship("Cost", foreign_keys="Cost.approved_by", back_populates="approver", lazy="noload")
    budgets_owned = relationship("Budget", foreign_keys="Budget.owner_id", back_populates="owner", lazy="noload")
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    
    processed_debt_payments = relationship(
        "DebtPayment", 
        foreign_keys="DebtPayment.processed_by", 
        back_populates="processor", 
        lazy="noload",
        overlaps="payments_processed" # Correction SAWarning
    )
    # Ajouter cette relation
    subscription = relationship(
        "UserSubscription", 
        back_populates="user", 
        uselist=False,
        cascade="all, delete-orphan"
    )

    # =========================
    # Méthodes utilitaires
    # =========================
    def __repr__(self) -> str:
        return f"<User {self.email} ({self.role})>"

    def get_primary_pharmacy(self):
        """Récupère la pharmacie marquée comme principale via l'association"""
        for assoc in self.pharmacy_associations:
            if assoc.is_primary:
                return assoc.pharmacy
        
        # Fallback 1: Première pharmacie de la liste
        if self.pharmacies:
            return self.pharmacies[0]
            
        return None

    def has_access_to_pharmacy(self, pharmacy_id: int) -> bool:
        """Vérifie si l'utilisateur est lié à une pharmacie spécifique"""
        return any(assoc.pharmacy_id == pharmacy_id for assoc in self.pharmacy_associations)

    def can_manage_pharmacy(self, pharmacy_id: int) -> bool:
        """Vérifie si l'utilisateur a les droits de gestion ou est admin"""
        if self.role in ["admin", "super_admin"]:
            return True
        for assoc in self.pharmacy_associations:
            if assoc.pharmacy_id == pharmacy_id:
                return assoc.can_manage
        return False

    def to_dict(self, include_tenant: bool = False, include_pharmacies: bool = False) -> dict:
        """Retour JSON-safe sans données sensibles"""
        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "nom_complet": self.nom_complet,
            "email": self.email,
            "role": self.role,
            "actif": self.actif,
            "telephone": self.telephone,
            "adresse": self.adresse,
            "permissions": self.permissions or {},
            "date_creation": self.date_creation.isoformat() if self.date_creation else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }

        if include_tenant and self.tenant:
            data["tenant"] = {
                "id": str(self.tenant.id),
                "nom_pharmacie": self.tenant.nom_pharmacie,
                "ville": self.tenant.ville,
            }
        
        if include_pharmacies:
            data["pharmacies"] = [
                {
                    "id": assoc.pharmacy.id,
                    "name": assoc.pharmacy.name,
                    "is_primary": assoc.is_primary,
                    "can_manage": assoc.can_manage
                }
                for assoc in self.pharmacy_associations
            ]

        return data

    def has_permission(self, permission: str) -> bool:
        if self.role in {"super_admin", "admin"}:
            return True
        return bool(self.permissions.get(permission, False)) if self.permissions else False

    @property
    def is_active(self) -> bool:
        return self.actif is True

    def update_last_login(self):
        self.last_login = datetime.utcnow()
    
    # Méthodes pour gérer l'abonnement
    def get_subscription_status(self):
        """Retourne le statut complet de l'abonnement"""
        if not self.subscription:
            return {
                "has_subscription": False,
                "mode": "READ_ONLY",
                "message": "Aucun abonnement trouvé"
            }
        
        return {
            "has_subscription": True,
            "plan": self.subscription.plan_type,
            "plan_name": self.subscription.plan_name,
            "status": self.subscription.status,
            "mode": self.subscription.get_mode(),
            "is_active": self.subscription.is_active(),
            "is_trial": self.subscription.is_trial(),
            "days_remaining": self.subscription.days_remaining(),
            "end_date": self.subscription.end_date.isoformat() if self.subscription.end_date else None,
            "trial_end_date": self.subscription.trial_end_date.isoformat() if self.subscription.trial_end_date else None
        }

    def can_create_pharmacy(self) -> bool:
        """Vérifie si l'admin peut créer une nouvelle pharmacie"""
        if self.role != "admin":
            return False
        
        if not self.subscription or not self.subscription.is_active():
            return False
        
        # Compter les pharmacies actuelles
        from app.models.pharmacy import Pharmacy
        # Note: Cette méthode nécessite une session DB, à utiliser avec précaution
        # Idéalement, on passe par un service
        return True  # Logique à implémenter dans un service

    def can_add_user(self) -> bool:
        """Vérifie si l'admin peut ajouter un nouvel utilisateur"""
        if self.role != "admin":
            return False
        
        if not self.subscription or not self.subscription.is_active():
            return False
        
        return True  # Logique à implémenter dans un service