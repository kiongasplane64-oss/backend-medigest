# app/models/customer.py
import uuid
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Date, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base


class Customer(Base):
    __tablename__ = "customers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="CASCADE"), nullable=False)
    
    # Informations personnelles
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True, unique=True)
    phone = Column(String(50), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    branch_id = sa.Column(sa.UUID, sa.ForeignKey("branches.id"), nullable=True)
    # Date de naissance
    birth_date = Column(Date, nullable=True)
    
    # Informations médicales
    blood_type = Column(String(5), nullable=True)
    allergies = Column(Text, nullable=True)
    medical_notes = Column(Text, nullable=True)
    
    # Assurance
    insurance_provider = Column(String(255), nullable=True)
    insurance_number = Column(String(100), nullable=True)
    
    # Statut
    is_active = Column(Boolean, default=True)
    is_vip = Column(Boolean, default=False)
    loyalty_points = Column(Integer, default=0)
    
    # Métadonnées
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Relations
    tenant = relationship("Tenant")
    pharmacy = relationship("Pharmacy", back_populates="customers")
    creator = relationship("User", foreign_keys=[created_by])
    sales = relationship("Sale", back_populates="customer")
    branch = relationship("Branch", back_populates="customers")
    orders = relationship("Order", back_populates="customer", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<Customer {self.first_name} {self.last_name}>"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"