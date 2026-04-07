# app/models/subscription_code.py
"""
Modèle pour les codes d'activation d'abonnement.
Version adaptée pour l'architecture "abonnement par pharmacie".
"""

import uuid
from datetime import datetime, timedelta
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Integer, Enum, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import enum

from app.db.base import Base


class SubscriptionCodeStatus(str, enum.Enum):
    """Statut d'un code d'abonnement."""
    PENDING = "pending"      # En attente d'activation
    ACTIVATED = "activated"  # Activé et utilisé
    EXPIRED = "expired"      # Expiré
    CANCELLED = "cancelled"  # Annulé


class SubscriptionCode(Base):
    """
    Code d'activation d'abonnement.
    Permet d'activer un abonnement pour une pharmacie via un code promo.
    """
    __tablename__ = "subscription_codes"

    # Identifiant
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Code unique
    code = Column(String(50), unique=True, index=True, nullable=False)
    
    # Plan associé
    plan_type = Column(String(50), nullable=False)  # trial, starter, professional, enterprise, infinite
    plan_name = Column(String(100), nullable=False)
    
    # Durée de l'abonnement activé
    duration_days = Column(Integer, nullable=False)  # 30, 365, etc.
    
    # Prix (0 pour gratuit)
    price = Column(Integer, nullable=False, default=0)
    currency = Column(String(3), default="EUR")
    
    # Période de validité du code (quand le code peut être utilisé)
    valid_from = Column(DateTime, default=datetime.utcnow)
    valid_until = Column(DateTime, nullable=True)
    
    # Utilisation du code
    status = Column(Enum(SubscriptionCodeStatus), default=SubscriptionCodeStatus.PENDING)
    
    # Pharmacie qui a utilisé le code (NOUVEAU - lié à la pharmacie)
    activated_for_pharmacy_id = Column(UUID(as_uuid=True), ForeignKey("pharmacies.id", ondelete="SET NULL"), nullable=True)
    activated_for_pharmacy = relationship("Pharmacy", foreign_keys=[activated_for_pharmacy_id])
    
    # Utilisateur qui a activé le code
    activated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    activated_by_user = relationship("User", foreign_keys=[activated_by_user_id])
    activated_at = Column(DateTime, nullable=True)
    
    # Qui a créé le code (super admin)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_by_user = relationship("User", foreign_keys=[created_by_user_id])
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Tenant (optionnel - pour filtrer)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    
    # Métadonnées
    notes = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    
    # Configuration supplémentaire
    config = Column(JSON, default=lambda: {
        "max_uses": 1,  # Nombre maximum d'utilisations (1 par défaut)
        "used_count": 0,  # Nombre d'utilisations
        "bulk_codes": False,  # Si c'est un code générique utilisable plusieurs fois
        "allowed_pharmacy_types": [],  # Types de pharmacies autorisés
        "requires_approval": False,  # Nécessite une approbation manuelle
    })
    
    def is_valid(self) -> bool:
        """
        Vérifie si le code est encore valide pour être utilisé.
        """
        now = datetime.utcnow()
        
        # Vérifier le statut
        if self.status != SubscriptionCodeStatus.PENDING:
            return False
        
        # Vérifier la période de validité
        if self.valid_until and now > self.valid_until:
            return False
        if self.valid_from and now < self.valid_from:
            return False
        
        # Vérifier le nombre d'utilisations
        if not self.config.get("bulk_codes", False):
            if self.config.get("used_count", 0) >= self.config.get("max_uses", 1):
                return False
        
        return True
    
    def days_remaining(self) -> int:
        """
        Jours restants avant expiration du code.
        """
        if not self.valid_until:
            return 365
        delta = self.valid_until - datetime.utcnow()
        return max(0, delta.days)
    
    def mark_as_activated(self, pharmacy_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """
        Marque le code comme activé et l'associe à une pharmacie.
        """
        self.status = SubscriptionCodeStatus.ACTIVATED
        self.activated_for_pharmacy_id = pharmacy_id
        self.activated_by_user_id = user_id
        self.activated_at = datetime.utcnow()
        
        # Incrémenter le compteur d'utilisations
        config = self.config or {}
        config["used_count"] = config.get("used_count", 0) + 1
        self.config = config
    
    def can_be_used_for_pharmacy(self, pharmacy_type: str = None) -> bool:
        """
        Vérifie si le code peut être utilisé pour un type de pharmacie spécifique.
        """
        if not self.is_valid():
            return False
        
        allowed_types = self.config.get("allowed_pharmacy_types", [])
        if allowed_types and pharmacy_type not in allowed_types:
            return False
        
        return True
    
    def __repr__(self) -> str:
        return f"<SubscriptionCode {self.code} - {self.plan_type} ({self.status.value})>"