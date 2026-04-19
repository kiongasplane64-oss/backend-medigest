# app/models/user.py
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON, Integer, func
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
    # Dates (CORRIGÉ - une seule colonne création)
    # =========================
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_login = Column(DateTime, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # =========================
    # Session active (pharmacie et branche)
    # =========================
    active_pharmacy_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
        nullable=True
    )
    active_branch_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True
    )

    # =========================
    # Relations
    # =========================
    tenant = relationship("Tenant", back_populates="users", foreign_keys=[tenant_id])
    
    # Pharmacie active
    active_pharmacy = relationship(
        "Pharmacy",
        foreign_keys=[active_pharmacy_id],
        lazy="joined"
    )
    
    # Branche active
    active_branch = relationship(
        "Branch",
        foreign_keys=[active_branch_id],
        lazy="joined"
    )
    
    # Relation Many-to-Many directe vers les pharmacies
    pharmacies = relationship(
        "Pharmacy",
        secondary="user_pharmacy",
        back_populates="users",
        overlaps="pharmacy_associations,user_associations,user,pharmacy"
    )

    # Relation vers la table d'association
    pharmacy_associations = relationship(
        "UserPharmacy",
        back_populates="user",
        cascade="all, delete-orphan",
        overlaps="pharmacies,users"
    )

    # Relations inverses
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
        overlaps="payments_processed"
    )
    expenses = relationship("Expense", foreign_keys="Expense.user_id", back_populates="user")
    expenses_approved = relationship("Expense", foreign_keys="Expense.approved_by")
    user_expenses = relationship("UserExpense", back_populates="user", foreign_keys="UserExpense.user_id")

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
        
        if self.pharmacies:
            return self.pharmacies[0]
            
        return None

    def set_active_pharmacy(self, pharmacy_id):
        """Définit la pharmacie active pour l'utilisateur"""
        # Vérifier que l'utilisateur a accès à cette pharmacie
        if self.has_access_to_pharmacy(pharmacy_id):
            self.active_pharmacy_id = pharmacy_id
            return True
        return False
    
    def set_active_branch(self, branch_id):
        """Définit la branche active pour l'utilisateur"""
        # Vérifier que la branche existe et appartient à la pharmacie active
        if self.active_pharmacy_id:
            from app.models.branch import Branch
            # La vérification se fera via le service
            self.active_branch_id = branch_id
            return True
        return False

    def has_access_to_pharmacy(self, pharmacy_id) -> bool:
        """Vérifie si l'utilisateur est lié à une pharmacie spécifique"""
        return any(assoc.pharmacy_id == pharmacy_id for assoc in self.pharmacy_associations)

    def can_manage_pharmacy(self, pharmacy_id) -> bool:
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "active_pharmacy_id": str(self.active_pharmacy_id) if self.active_pharmacy_id else None,
            "active_branch_id": str(self.active_branch_id) if self.active_branch_id else None,
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
                    "id": str(assoc.pharmacy.id),
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
    
    # ========== MÉTHODES D'ABONNEMENT PAR BRANCHE ==========
    
    def get_active_pharmacy_subscription_status(self, db_session=None):
        """
        Retourne le statut de l'abonnement de la pharmacie active de l'utilisateur.
        Si aucune pharmacie active, retourne un statut inactif.
        """
        if not self.active_pharmacy_id:
            return {
                "has_subscription": False,
                "mode": "READ_ONLY",
                "message": "Aucune pharmacie active sélectionnée"
            }
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return self._get_pharmacy_subscription_status(db_session)
            finally:
                db_session.close()
        
        return self._get_pharmacy_subscription_status(db_session)
    
    def _get_pharmacy_subscription_status(self, db_session):
        """Méthode interne pour récupérer le statut d'abonnement d'une pharmacie"""
        from app.services.pharmacy_subscription_service import check_pharmacy_subscription
        
        try:
            result = check_pharmacy_subscription(
                db_session, 
                self.active_pharmacy_id, 
                raise_if_inactive=False
            )
            
            if not result.get("has_subscription") or not result.get("is_active"):
                return {
                    "has_subscription": False,
                    "mode": "READ_ONLY",
                    "message": "Abonnement de la pharmacie inactif ou expiré",
                    "plan": result.get("plan"),
                    "days_remaining": result.get("days_remaining", 0)
                }
            
            return {
                "has_subscription": True,
                "mode": "FULL",
                "plan": result.get("plan"),
                "plan_name": result.get("plan_name"),
                "is_active": True,
                "days_remaining": result.get("days_remaining", 0),
                "end_date": result.get("end_date"),
                "max_products": result.get("max_products"),
                "max_users": result.get("max_users"),
                "is_unlimited_products": result.get("is_unlimited_products", False),
                "is_unlimited_users": result.get("is_unlimited_users", False)
            }
        except Exception as e:
            return {
                "has_subscription": False,
                "mode": "READ_ONLY",
                "message": f"Erreur de vérification: {str(e)}"
            }
    
    def get_subscription_mode(self, db_session=None) -> str:
        """
        Retourne le mode d'accès: "FULL" ou "READ_ONLY"
        Basé sur l'abonnement de la pharmacie active.
        """
        status = self.get_active_pharmacy_subscription_status(db_session)
        return status.get("mode", "READ_ONLY")
    
    def can_create_product(self, db_session=None) -> bool:
        """
        Vérifie si l'utilisateur peut créer un produit dans sa pharmacie active.
        Basé sur l'abonnement de la pharmacie et les limites.
        """
        if not self.active_pharmacy_id:
            return False
        
        from app.services.pharmacy_subscription_service import can_add_product
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return can_add_product(db_session, self.active_pharmacy_id)
            finally:
                db_session.close()
        
        return can_add_product(db_session, self.active_pharmacy_id)
    
    def can_add_user_to_pharmacy(self, db_session=None) -> bool:
        """
        Vérifie si l'admin peut ajouter un utilisateur à la pharmacie active.
        """
        if self.role != "admin":
            return False
        
        if not self.active_pharmacy_id:
            return False
        
        from app.services.pharmacy_subscription_service import can_add_user_to_pharmacy
        
        if not db_session:
            from app.db.session import SessionLocal
            db_session = SessionLocal()
            try:
                return can_add_user_to_pharmacy(db_session, self.active_pharmacy_id)
            finally:
                db_session.close()
        
        return can_add_user_to_pharmacy(db_session, self.active_pharmacy_id)
    
    def can_create_pharmacy(self) -> bool:
        """
        Vérifie si l'admin peut créer une nouvelle pharmacie.
        Note: Dans la nouvelle architecture, le nombre de pharmacies est illimité,
        donc toujours True pour les admins actifs.
        """
        if self.role != "admin":
            return False
        
        if not self.actif:
            return False
        
        return True

    def can_add_user(self, db_session=None) -> bool:
        """
        Alias pour can_add_user_to_pharmacy (compatibilité)
        """
        return self.can_add_user_to_pharmacy(db_session)