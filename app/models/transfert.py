# app/models/transfer.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey, Numeric, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base
import enum

class TransferStatus(enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TransferType(enum.Enum):
    INTERNAL = "internal"  # Entre pharmacies du même tenant
    EXTERNAL = "external"  # Vers une autre pharmacie (différent tenant)

class ProductTransfer(Base):
    """
    Modèle pour gérer les transferts de produits entre pharmacies
    """
    __tablename__ = "product_transfers"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, index=True)
    
    # =====================================
    # PHARMACIES SOURCE ET DESTINATION
    # =====================================
    from_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey('pharmacies.id'), nullable=False)
    to_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey('pharmacies.id'), nullable=False)
    
    # =====================================
    # INFORMATION DU TRANSFERT
    # =====================================
    transfer_number = Column(String(50), unique=True, nullable=False, index=True)
    transfer_type = Column(Enum(TransferType), default=TransferType.INTERNAL)
    status = Column(Enum(TransferStatus), default=TransferStatus.PENDING)
    
    # =====================================
    # DATES
    # =====================================
    requested_date = Column(DateTime, default=datetime.utcnow)
    approved_date = Column(DateTime, nullable=True)
    completed_date = Column(DateTime, nullable=True)
    expected_delivery_date = Column(DateTime, nullable=True)
    
    # =====================================
    # INFORMATIONS SUPPLÉMENTAIRES
    # =====================================
    reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    
    # =====================================
    # UTILISATEURS
    # =====================================
    requested_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    received_by_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=True)
    
    # =====================================
    # STATISTIQUES
    # =====================================
    total_items = Column(Integer, default=0)
    total_quantity = Column(Integer, default=0)
    total_value = Column(Numeric(12, 2), default=0.0)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant")
    from_pharmacy = relationship("Pharmacy", foreign_keys=[from_pharmacy_id])
    to_pharmacy = relationship("Pharmacy", foreign_keys=[to_pharmacy_id])
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    approved_by = relationship("User", foreign_keys=[approved_by_id])
    received_by = relationship("User", foreign_keys=[received_by_id])
    
    # Relation avec les items du transfert
    items = relationship("TransferItem", back_populates="transfer", cascade="all, delete-orphan")
    
    def __repr__(self):
        return f"<ProductTransfer {self.transfer_number} ({self.status.value})>"

class TransferItem(Base):
    """
    Modèle pour les items d'un transfert
    """
    __tablename__ = "transfer_items"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transfer_id = Column(UUID(as_uuid=True), ForeignKey('product_transfers.id'), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey('products.id'), nullable=False)
    
    # =====================================
    # INFORMATIONS DU PRODUIT
    # =====================================
    product_code = Column(String(50), nullable=True)
    product_name = Column(String(200), nullable=False)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(DateTime, nullable=True)
    
    # =====================================
    # QUANTITÉS
    # =====================================
    requested_quantity = Column(Integer, nullable=False)
    approved_quantity = Column(Integer, nullable=True)
    transferred_quantity = Column(Integer, default=0)
    received_quantity = Column(Integer, default=0)
    
    # =====================================
    # PRIX
    # =====================================
    unit_price = Column(Numeric(12, 2), nullable=False)
    total_price = Column(Numeric(12, 2), nullable=False)
    
    # =====================================
    # STATUT
    # =====================================
    status = Column(Enum(TransferStatus), default=TransferStatus.PENDING)
    
    # =====================================
    # NOTES
    # =====================================
    notes = Column(Text, nullable=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =====================================
    # RELATIONS
    # =====================================
    transfer = relationship("ProductTransfer", back_populates="items")
    product = relationship("Product")
    
    def __repr__(self):
        return f"<TransferItem {self.product_name} x{self.requested_quantity}>"