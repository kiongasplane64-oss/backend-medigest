# app/models/branch.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Float
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class Branch(Base):
    """
    Modèle pour les succursales/branches d'une pharmacie
    """
    __tablename__ = "branches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    parent_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    
    # Informations de base
    name = Column(String(255), nullable=False)
    code = Column(String(50), unique=True, nullable=True)
    
    # Localisation
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    country = Column(String(100), nullable=False, default="RDC")
    phone = Column(String(50))
    email = Column(String(255))
    
    # Coordonnées GPS
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # Responsable
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_name = Column(String(255), nullable=True)
    
    # Horaires d'ouverture
    opening_hours = Column(JSON, default=lambda: {
        "monday": "08:00-20:00",
        "tuesday": "08:00-20:00",
        "wednesday": "08:00-20:00",
        "thursday": "08:00-20:00",
        "friday": "08:00-20:00",
        "saturday": "09:00-18:00",
        "sunday": "closed"
    })
    
    # Configuration spécifique à la succursale
    config = Column(JSON, default=lambda: {
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        "enableBatchTracking": True,
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00"
        }
    })
    
    # Statut
    is_active = Column(Boolean, default=True)
    is_main_branch = Column(Boolean, default=False)
    
    # Métadonnées
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # =========================
    # Relations
    # =========================
    tenant = relationship("Tenant")
    parent_pharmacy = relationship("Pharmacy", back_populates="branches")
    manager = relationship("User", foreign_keys=[manager_id])
    creator = relationship("User", foreign_keys=[created_by])
    
    # Relations avec les autres modèles
    products = relationship("Product", back_populates="branch")
    sales = relationship("Sale", back_populates="branch")
    customers = relationship("Customer", back_populates="branch")
    capitals = relationship("Capital", back_populates="branch")
    # =========================
    # Méthodes
    # =========================
    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "code": self.code,
            "address": self.address,
            "city": self.city,
            "phone": self.phone,
            "email": self.email,
            "manager_name": self.manager_name,
            "is_active": self.is_active,
            "is_main_branch": self.is_main_branch,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
    
    def __repr__(self):
        return f"<Branch {self.name} - {self.city}>"