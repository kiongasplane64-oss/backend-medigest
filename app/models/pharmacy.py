# app/models/pharmacy.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship, Session
from datetime import datetime
from app.db.base import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid

class Pharmacy(Base):
    __tablename__ = "pharmacies"
    
    # =========================
    # Identité & Localisation
    # =========================
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True
    )

    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    
    # CORRECTION : Une seule déclaration de license_number
    license_number = Column(String(100), nullable=False, default="PENDING")
    
    # AJOUT : Champ pharmacy_code qui manquait
    pharmacy_code = Column(String(50), unique=True, nullable=True)
    
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    country = Column(String(100), nullable=False, default="RDC")
    phone = Column(String(50))
    email = Column(String(255))
    
    # =========================
    # Statut & Spécialisation
    # =========================
    is_active = Column(Boolean, default=True)
    is_main = Column(Boolean, default=False, comment="Pharmacie principale du tenant")
    opening_hours = Column(JSON)  # {"monday": "08:00-20:00", ...}
    pharmacist_in_charge = Column(String(255))
    pharmacist_license = Column(String(100))
    
    # =========================
    # Configuration SaaS
    # =========================
    config = Column(JSON, default=lambda: {
        "require_prescription": True,
        "enable_expiry_alerts": True,
        "low_stock_threshold": 10,
        "enable_barcode": True,
        "tax_rate": 18.0,
        "currency": "CDF",
        "language": "fr",
        "date_format": "dd/MM/yyyy",
        "decimal_precision": 2
    })
    
    # =========================
    # Métadonnées
    # =========================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =========================
    # Relations (Mise à jour Overlaps)
    # =========================
    tenant = relationship("Tenant", back_populates="pharmacies")
    
    # Relation Many-to-Many directe vers les utilisateurs
    users = relationship(
        "User",
        secondary="user_pharmacy",
        back_populates="pharmacies",
        overlaps="pharmacy_associations,user_associations,user,pharmacy"
    )

    # Relation vers la table d'association (détails de l'accès)
    user_associations = relationship(
        "UserPharmacy",
        back_populates="pharmacy",
        cascade="all, delete-orphan",
        overlaps="users,pharmacies"
    )

    # Autres modules
    products = relationship("Product", back_populates="pharmacy", cascade="all, delete-orphan")
    sales = relationship("Sale", back_populates="pharmacy", cascade="all, delete-orphan")
    
    # =========================
    # Méthodes
    # =========================
    def __repr__(self):
        return f"<Pharmacy {self.name} ({self.license_number})>"
    
    def get_users_with_access(self, role_filter=None):
        """Récupère la liste des utilisateurs actifs liés à cette pharmacie"""
        # On utilise la relation 'users' directe
        users_list = [u for u in self.users if u.actif]
        if role_filter:
            return [u for u in users_list if u.role == role_filter]
        return users_list
    
    def add_user(self, db: Session, user_id: uuid.UUID, is_primary: bool = False, can_manage: bool = False, role_in_pharmacy: str = None):
        """
        Ajoute un utilisateur à la pharmacie via la table d'association.
        Note: Nécessite un db.commit() après l'appel.
        """
        from app.models.user_pharmacy import UserPharmacy
        
        # Vérification si l'association existe déjà
        existing = db.query(UserPharmacy).filter_by(
            user_id=user_id, 
            pharmacy_id=self.id
        ).first()
        
        if existing:
            existing.is_primary = is_primary
            existing.can_manage = can_manage
            if role_in_pharmacy:
                existing.role_in_pharmacy = role_in_pharmacy
            return existing

        new_assoc = UserPharmacy(
            user_id=user_id,
            pharmacy_id=self.id,
            is_primary=is_primary,
            can_manage=can_manage,
            role_in_pharmacy=role_in_pharmacy
        )
        db.add(new_assoc)
        return new_assoc