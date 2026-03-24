# app/models/user_session.py
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.db.base import Base


class UserSession(Base):
    """Modèle pour suivre les sessions utilisateur multi-plateformes"""
    __tablename__ = "user_sessions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    
    # Identifiants de session
    session_id = Column(String(255), unique=True, nullable=False)
    refresh_token = Column(Text, nullable=True)
    
    # Informations plateforme
    platform = Column(String(50), nullable=False)  # web, mobile, pos, tablet
    device_type = Column(String(50))  # desktop, mobile, tablet, pos_terminal
    device_name = Column(String(255))  # iPhone 14, Samsung Galaxy, etc.
    browser = Column(String(100))  # Chrome, Safari, etc.
    browser_version = Column(String(50))
    os = Column(String(100))  # iOS, Android, Windows, macOS
    os_version = Column(String(50))
    
    # Informations réseau
    ip_address = Column(String(45))
    user_agent = Column(Text)
    
    # Métadonnées
    location_city = Column(String(100))
    location_country = Column(String(100))
    
    # Statut
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_activity = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relations
    user = relationship("User", back_populates="sessions", foreign_keys=[user_id])
    tenant = relationship("Tenant", back_populates="sessions", foreign_keys=[tenant_id])
    
    # Index
    __table_args__ = (
        Index("idx_user_sessions_user_id", "user_id"),
        Index("idx_user_sessions_tenant_id", "tenant_id"),
        Index("idx_user_sessions_session_id", "session_id"),
        Index("idx_user_sessions_is_active", "is_active"),
        Index("idx_user_sessions_expires_at", "expires_at"),
        Index("idx_user_sessions_platform", "platform"),
        Index("idx_user_sessions_last_activity", "last_activity"),
    )
    
    def __repr__(self) -> str:
        return f"<UserSession {self.session_id} - {self.platform}>"
    
    def is_expired(self) -> bool:
        """Vérifie si la session est expirée"""
        return datetime.utcnow() > self.expires_at
    
    def extend_session(self, days: int = 30) -> None:
        """Prolonge la session"""
        from datetime import timedelta
        self.expires_at = datetime.utcnow() + timedelta(days=days)
        self.last_activity = datetime.utcnow()
    
    def update_activity(self) -> None:
        """Met à jour la dernière activité"""
        self.last_activity = datetime.utcnow()