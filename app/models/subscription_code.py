# app/models/subscription_code.py
import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Integer, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base

class SubscriptionCodeStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVATED = "activated"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class SubscriptionCode(Base):
    __tablename__ = "subscription_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code = Column(String(50), unique=True, index=True, nullable=False)
    plan_type = Column(String(50), nullable=False)
    plan_name = Column(String(100), nullable=False)
    duration_days = Column(Integer, nullable=False)  # 30, 365, etc.
    price = Column(Integer, nullable=False)
    currency = Column(String(3), default="USD")
    
    
    # Période de validité du code
    valid_from = Column(DateTime, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=True)
    
    # Utilisation
    status = Column(Enum(SubscriptionCodeStatus), default=SubscriptionCodeStatus.PENDING)
    activated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    activated_by_user = relationship("User", foreign_keys=[activated_by_user_id])
    activated_at = Column(DateTime, nullable=True)
    
    # Qui a créé le code
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Associations avec tenant et utilisateur (NOUVEAUX CHAMPS)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    # Métadonnées
    notes = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Relations supplémentaires (NOUVELLES RELATIONS)
    tenant = relationship("Tenant", foreign_keys=[tenant_id], backref="subscription_codes")
    assigned_user = relationship("User", foreign_keys=[user_id], backref="assigned_codes")
    
    def is_valid(self) -> bool:
        """Vérifie si le code est encore valide"""
        now = datetime.utcnow()
        if self.status != SubscriptionCodeStatus.PENDING:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        if self.valid_from and now < self.valid_from:
            return False
        return True
    
    def days_remaining(self) -> int:
        """Jours restants avant expiration du code"""
        if not self.valid_until:
            return 365  # Valeur par défaut si pas de date d'expiration
        delta = self.valid_until - datetime.utcnow()
        return max(0, delta.days)