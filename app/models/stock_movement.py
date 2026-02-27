# app/models/stock_movement.py
import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, Text, Date, Index, DECIMAL
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class StockMovement(Base):
    """
    Modèle pour suivre les mouvements de stock
    """
    __tablename__ = "stock_movements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)
    # Dans la classe StockMovement
    product_stock_id = Column(UUID(as_uuid=True), ForeignKey("product_stocks.id"), nullable=True)
    product_stock = relationship("ProductStock", back_populates="stock_movements")

    # Quantités
    quantity_before = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_after = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_change = Column(DECIMAL(15, 3), nullable=False, default=0)

    # Prix
    unit_price = Column(DECIMAL(15, 2), nullable=True)
    total_price = Column(DECIMAL(15, 2), nullable=True)

    movement_type = Column(
        String(50),
        nullable=False,
        index=True,
        comment="initial, purchase, sale, adjustment, return, transfer, expiry, correction"
    )

    # Références
    reference = Column(String(100), nullable=True, index=True)
    document_number = Column(String(100), nullable=True)
    batch_number = Column(String(100), nullable=True)
    location_from = Column(String(100), nullable=True)
    location_to = Column(String(100), nullable=True)

    # Raison et notes
    reason = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)

    # Utilisateur responsable
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)

    # Dates
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expiration_date = Column(Date, nullable=True)

    # =======================
    # Relations
    # =======================
    tenant = relationship("Tenant")
    product = relationship("Product", back_populates="stock_movements")
    user = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_stock_movements_tenant_date", "tenant_id", "created_at"),
        Index("ix_stock_movements_product_date", "product_id", "created_at"),
        Index("ix_stock_movements_type_date", "movement_type", "created_at"),
        Index("ix_stock_movements_reference", "reference"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "product_id": str(self.product_id),
            "product_name": getattr(self.product, "name", None),
            "quantity_before": float(self.quantity_before or 0),
            "quantity_after": float(self.quantity_after or 0),
            "quantity_change": float(self.quantity_change or 0),
            "unit_price": float(self.unit_price) if self.unit_price is not None else None,
            "total_price": float(self.total_price) if self.total_price is not None else None,
            "movement_type": self.movement_type,
            "reference": self.reference,
            "document_number": self.document_number,
            "batch_number": self.batch_number,
            "reason": self.reason,
            "notes": self.notes,
            "created_by": str(self.created_by),
            "created_by_name": getattr(self.user, "nom_complet", None),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expiration_date": self.expiration_date.isoformat() if self.expiration_date else None,
        }

    def __repr__(self):
        return f"<StockMovement {self.movement_type} {self.quantity_change:+} for {self.product_id}>"


class InventoryCount(Base):
    """
    Modèle pour les inventaires physiques
    """
    __tablename__ = "inventory_counts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True)

    count_number = Column(String(50), nullable=False, unique=True, index=True)

    # IMPORTANT : ne mets PAS datetime.utcnow().date (ça s’évalue au chargement du module)
    count_date = Column(Date, nullable=False, default=date.today, index=True)

    location = Column(String(100), nullable=True)

    total_products = Column(Integer, nullable=False, default=0)
    counted_products = Column(Integer, nullable=False, default=0)
    discrepancies = Column(Integer, nullable=False, default=0)

    theoretical_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    actual_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    difference_value = Column(DECIMAL(15, 2), nullable=False, default=0)

    status = Column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
        comment="pending, in_progress, completed, validated, cancelled"
    )

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    validated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    validated_at = Column(DateTime, nullable=True)

    notes = Column(Text, nullable=True)

    tenant = relationship("Tenant")
    creator = relationship("User", foreign_keys=[created_by])
    validator = relationship("User", foreign_keys=[validated_by])

    __table_args__ = (
        Index("ix_inventory_counts_tenant_status", "tenant_id", "status"),
        Index("ix_inventory_counts_tenant_date", "tenant_id", "count_date"),
    )

    @property
    def progress_percentage(self) -> float:
        if not self.total_products:
            return 0.0
        return (self.counted_products / self.total_products) * 100.0

    @property
    def difference_percentage(self) -> float:
        if not self.theoretical_value:
            return 0.0
        return (float(self.difference_value or 0) / float(self.theoretical_value)) * 100.0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "count_number": self.count_number,
            "count_date": self.count_date.isoformat() if self.count_date else None,
            "location": self.location,
            "total_products": self.total_products,
            "counted_products": self.counted_products,
            "discrepancies": self.discrepancies,
            "theoretical_value": float(self.theoretical_value or 0),
            "actual_value": float(self.actual_value or 0),
            "difference_value": float(self.difference_value or 0),
            "difference_percentage": self.difference_percentage,
            "progress_percentage": self.progress_percentage,
            "status": self.status,
            "created_by": str(self.created_by),
            "validated_by": str(self.validated_by) if self.validated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
            "notes": self.notes,
        }

    def __repr__(self):
        return f"<InventoryCount {self.count_number} - {self.status}>"


class InventoryCountItem(Base):
    """
    Articles d'un inventaire physique
    """
    __tablename__ = "inventory_count_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inventory_count_id = Column(UUID(as_uuid=True), ForeignKey("inventory_counts.id"), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=False, index=True)

    theoretical_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    actual_quantity = Column(DECIMAL(15, 3), nullable=False, default=0)
    quantity_difference = Column(DECIMAL(15, 3), nullable=False, default=0)

    unit_price = Column(DECIMAL(15, 2), nullable=False, default=0)
    theoretical_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    actual_value = Column(DECIMAL(15, 2), nullable=False, default=0)
    value_difference = Column(DECIMAL(15, 2), nullable=False, default=0)

    batch_number = Column(String(100), nullable=True)
    location = Column(String(100), nullable=True)

    status = Column(String(20), nullable=False, default="pending", comment="pending, counted, validated")
    comments = Column(Text, nullable=True)

    counted_at = Column(DateTime, nullable=True)
    validated_at = Column(DateTime, nullable=True)

    inventory_count = relationship("InventoryCount", backref="items")
    product = relationship("Product")

    __table_args__ = (
        Index("ix_inventory_items_product", "product_id", "inventory_count_id"),
    )

    @property
    def has_discrepancy(self) -> bool:
        return float(self.quantity_difference or 0) != 0.0

    @property
    def discrepancy_percentage(self) -> float:
        tq = float(self.theoretical_quantity or 0)
        dq = float(self.quantity_difference or 0)
        aq = float(self.actual_quantity or 0)
        if tq == 0:
            return 100.0 if aq > 0 else 0.0
        return (abs(dq) / tq) * 100.0

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "product_id": str(self.product_id),
            "product_code": getattr(self.product, "code", None),
            "product_name": getattr(self.product, "name", None),
            "theoretical_quantity": float(self.theoretical_quantity or 0),
            "actual_quantity": float(self.actual_quantity or 0),
            "quantity_difference": float(self.quantity_difference or 0),
            "unit_price": float(self.unit_price or 0),
            "theoretical_value": float(self.theoretical_value or 0),
            "actual_value": float(self.actual_value or 0),
            "value_difference": float(self.value_difference or 0),
            "batch_number": self.batch_number,
            "location": self.location,
            "status": self.status,
            "has_discrepancy": self.has_discrepancy,
            "discrepancy_percentage": self.discrepancy_percentage,
            "comments": self.comments,
            "counted_at": self.counted_at.isoformat() if self.counted_at else None,
            "validated_at": self.validated_at.isoformat() if self.validated_at else None,
        }

    def __repr__(self):
        return f"<InventoryCountItem {self.product_id} diff: {self.quantity_difference}>"