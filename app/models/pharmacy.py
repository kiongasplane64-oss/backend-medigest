# app/models/pharmacy.py
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship, Session, validates
from datetime import datetime, time
from app.db.base import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid
import json
from typing import Optional, Dict, Any, List


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
    pharmacy_code = Column(String(50), unique=True, nullable=True)
    
    # Informations de licence
    license_number = Column(String(100), nullable=False, default="PENDING")
    license_issuing_authority = Column(String(255), nullable=True)
    license_expiry_date = Column(DateTime, nullable=True)
    
    # Localisation
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    country = Column(String(100), nullable=False, default="RDC")
    phone = Column(String(50))
    email = Column(String(255))
    website = Column(String(255), nullable=True)
    
    # Coordonnées GPS
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    
    # =========================
    # Statut & Spécialisation
    # =========================
    is_active = Column(Boolean, default=True)
    is_main = Column(Boolean, default=False, comment="Pharmacie principale du tenant")
    pharmacy_type = Column(String(50), default="retail", comment="retail, hospital, clinic")
    
    # Horaires d'ouverture (format simplifié)
    opening_hours = Column(JSON, default=lambda: {
        "monday": "08:00-20:00",
        "tuesday": "08:00-20:00",
        "wednesday": "08:00-20:00",
        "thursday": "08:00-20:00",
        "friday": "08:00-20:00",
        "saturday": "09:00-18:00",
        "sunday": "closed"
    })
    
    # Pharmacien responsable
    pharmacist_in_charge = Column(String(255))
    pharmacist_license = Column(String(100))
    pharmacist_contact = Column(String(50), nullable=True)
    
    # =========================
    # CONFIGURATION COMPLÈTE (version enrichie)
    # =========================
    config = Column(JSON, default=lambda: {
        # Informations de base
        "pharmacyInfo": {
            "name": "",
            "address": "",
            "phone": "",
            "email": "",
            "licenseNumber": "",
            "logo": None
        },
        
        # Devises et taux de change
        "currencies": [
            {"code": "CDF", "symbol": "FC", "isActive": True, "exchangeRate": 2500.0},
            {"code": "USD", "symbol": "$", "isActive": True, "exchangeRate": 1.0}
        ],
        "primaryCurrency": "CDF",
        "enableCurrencyConversion": True,
        
        # Fiscalité
        "taxRate": 16.0,
        "taxIncluded": True,
        "taxNumber": "",
        
        # Stock et alertes
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        "enableBatchTracking": True,
        "enableExpiryAlerts": True,
        "lowStockAlertEnabled": True,
        
        # Heures de service
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00",
            "overtimeEndTime": "22:00",
            "daysOff": {
                "monday": True,
                "tuesday": True,
                "wednesday": True,
                "thursday": True,
                "friday": True,
                "saturday": True,
                "sunday": False
            }
        },
        
        # Retour produit
        "productReturnDays": 30,
        "enableProductReturns": True,
        "requireReturnReason": True,
        
        # Configuration des prix et marges
        "marginConfig": {
            "defaultMargin": 25.0,
            "minMargin": 10.0,
            "maxMargin": 50.0
        },
        "automaticPricing": {
            "enabled": False,
            "method": "percentage",  # percentage, coefficient, margin
            "value": 25.0
        },
        
        # Thème et apparence
        "theme": "system",
        "enableDarkMode": True,
        
        # Capital et finances
        "initialCapital": 0.0,
        "currencySymbol": "FC",
        "decimalPrecision": 2,
        "dateFormat": "dd/MM/yyyy",
        
        # Configuration des branches/succursales
        "branchConfig": {
            "maxBranches": 1,
            "currentBranches": 0,
            "branches": []
        },
        
        # Paramètres généraux
        "language": "fr",
        "enableBarcode": True,
        "enablePrescriptionTracking": True,
        "enableLoyaltyProgram": False,
        
        # Métadonnées
        "createdAt": None,
        "updatedAt": None,
        "version": "1.0.0"
    })
    
    # Métadonnées additionnelles
    meta_data = Column(JSON, default=lambda: {
        "total_sales": 0,
        "total_products": 0,
        "total_customers": 0,
        "last_inventory_date": None,
        "subscription_status": "active"
    })
    
    # =========================
    # Métadonnées temporelles
    # =========================
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =========================
    # Relations
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
    purchases = relationship("Purchase", back_populates="pharmacy", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="pharmacy", cascade="all, delete-orphan")
    branches = relationship("Branch", back_populates="parent_pharmacy", cascade="all, delete-orphan")
    
    # =========================
    # Validations
    # =========================
    @validates('email')
    def validate_email(self, key, email):
        if email and '@' not in email:
            raise ValueError("Email invalide")
        return email
    
    @validates('phone')
    def validate_phone(self, key, phone):
        if phone and not phone.replace('+', '').replace(' ', '').isdigit():
            raise ValueError("Numéro de téléphone invalide")
        return phone
    
    # =========================
    # Méthodes de configuration
    # =========================
    def get_config(self, path: Optional[str] = None) -> Any:
        """
        Récupère la configuration complète ou une partie spécifique
        Exemple: pharmacy.get_config("workingHours.startTime")
        """
        if not path:
            return self.config
        
        keys = path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return None
        return value
    
    def update_config(self, updates: Dict[str, Any]) -> None:
        """
        Met à jour la configuration de manière récursive
        """
        def deep_update(original, updates):
            for key, value in updates.items():
                if isinstance(value, dict) and key in original and isinstance(original[key], dict):
                    deep_update(original[key], value)
                else:
                    original[key] = value
            return original
        
        self.config = deep_update(self.config or {}, updates)
        self.config["updatedAt"] = datetime.utcnow().isoformat()
    
    def reset_config_to_defaults(self) -> None:
        """Réinitialise la configuration aux valeurs par défaut"""
        self.config = self.__table__.c.config.default.arg()
        self.config["updatedAt"] = datetime.utcnow().isoformat()
    
    # =========================
    # Gestion des succursales
    # =========================
    def add_branch(self, branch_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ajoute une nouvelle succursale
        """
        branch_config = self.get_config("branchConfig") or {
            "maxBranches": 1,
            "currentBranches": 0,
            "branches": []
        }
        
        if branch_config["currentBranches"] >= branch_config["maxBranches"]:
            raise ValueError("Limite de succursales atteinte")
        
        new_branch = {
            "id": str(uuid.uuid4()),
            "name": branch_data.get("name", f"Succursale {branch_config['currentBranches'] + 1}"),
            "address": branch_data.get("address", ""),
            "phone": branch_data.get("phone", ""),
            "email": branch_data.get("email", ""),
            "manager": branch_data.get("manager", ""),
            "created_at": datetime.utcnow().isoformat(),
            "is_active": True,
            "config": branch_data.get("config", {})
        }
        
        branch_config["branches"].append(new_branch)
        branch_config["currentBranches"] = len(branch_config["branches"])
        
        self.update_config({"branchConfig": branch_config})
        return new_branch
    
    def get_branch(self, branch_id: str) -> Optional[Dict[str, Any]]:
        """Récupère une succursale par son ID"""
        branches = self.get_config("branchConfig.branches") or []
        for branch in branches:
            if branch.get("id") == branch_id:
                return branch
        return None
    
    def update_branch(self, branch_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Met à jour une succursale"""
        branches = self.get_config("branchConfig.branches") or []
        for i, branch in enumerate(branches):
            if branch.get("id") == branch_id:
                branches[i].update(updates)
                branches[i]["updated_at"] = datetime.utcnow().isoformat()
                self.update_config({"branchConfig": {"branches": branches}})
                return branches[i]
        return None
    
    def remove_branch(self, branch_id: str) -> bool:
        """Supprime (désactive) une succursale"""
        branches = self.get_config("branchConfig.branches") or []
        for i, branch in enumerate(branches):
            if branch.get("id") == branch_id:
                branches[i]["is_active"] = False
                self.update_config({"branchConfig": {"branches": branches}})
                return True
        return False
    
    # =========================
    # Gestion des devises
    # =========================
    def get_active_currencies(self) -> List[Dict[str, Any]]:
        """Récupère les devises actives"""
        currencies = self.get_config("currencies") or []
        return [c for c in currencies if c.get("isActive", False)]
    
    def convert_amount(self, amount: float, from_currency: str, to_currency: str) -> float:
        """
        Convertit un montant d'une devise à une autre
        """
        currencies = self.get_config("currencies") or []
        
        # Trouver les taux
        from_rate = None
        to_rate = None
        
        for c in currencies:
            if c["code"] == from_currency:
                from_rate = c["exchangeRate"]
            if c["code"] == to_currency:
                to_rate = c["exchangeRate"]
        
        if not from_rate or not to_rate:
            raise ValueError(f"Devise non trouvée: {from_currency if not from_rate else to_currency}")
        
        # Conversion via USD comme référence
        amount_in_usd = amount / from_rate if from_currency != "USD" else amount
        converted = amount_in_usd * to_rate if to_currency != "USD" else amount_in_usd
        
        return round(converted, self.get_config("decimalPrecision") or 2)
    
    # =========================
    # Vérification des heures de service
    # =========================
    def is_in_service(self, check_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Vérifie si la pharmacie est en service à l'heure donnée
        """
        if check_time is None:
            check_time = datetime.utcnow()
        
        working_hours = self.get_config("workingHours") or {}
        
        if not working_hours.get("enabled", True):
            return {
                "in_service": True,
                "message": "Service toujours disponible",
                "restrictions_enabled": False
            }
        
        current_time = check_time.hour * 60 + check_time.minute
        current_day = check_time.strftime("%A").lower()
        
        # Mapping des jours
        day_mapping = {
            "monday": "monday",
            "tuesday": "tuesday",
            "wednesday": "wednesday",
            "thursday": "thursday",
            "friday": "friday",
            "saturday": "saturday",
            "sunday": "sunday"
        }
        
        days_off = working_hours.get("daysOff", {})
        is_working_day = days_off.get(day_mapping.get(current_day, ""), False)
        
        start_time = working_hours.get("startTime", "08:00").split(":")
        end_time = working_hours.get("endTime", "20:00").split(":")
        
        start_minutes = int(start_time[0]) * 60 + int(start_time[1])
        end_minutes = int(end_time[0]) * 60 + int(end_time[1])
        
        is_within_hours = current_time >= start_minutes and current_time <= end_minutes
        in_service = is_working_day and is_within_hours
        
        return {
            "in_service": in_service,
            "restrictions_enabled": True,
            "current_time_utc": check_time.isoformat(),
            "current_day": current_day,
            "is_working_day": is_working_day,
            "is_within_hours": is_within_hours,
            "working_hours": {
                "start": working_hours.get("startTime"),
                "end": working_hours.get("endTime"),
                "overtime": working_hours.get("overtimeEndTime")
            },
            "message": "En service" if in_service else "Hors service"
        }
    
    # =========================
    # Méthodes de gestion des utilisateurs
    # =========================
    def get_users_with_access(self, role_filter: Optional[str] = None) -> List:
        """Récupère la liste des utilisateurs actifs liés à cette pharmacie"""
        users_list = [u for u in self.users if hasattr(u, 'actif') and u.actif]
        if role_filter:
            return [u for u in users_list if hasattr(u, 'role') and u.role == role_filter]
        return users_list
    
    def add_user(self, db: Session, user_id: uuid.UUID, is_primary: bool = False, 
                 can_manage: bool = False, role_in_pharmacy: Optional[str] = None):
        """
        Ajoute un utilisateur à la pharmacie via la table d'association.
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
    
    # =========================
    # Méthodes de reporting
    # =========================
    def get_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques de la pharmacie"""
        return {
            "pharmacy_id": str(self.id),
            "name": self.name,
            "total_products": len(self.products) if hasattr(self, 'products') else 0,
            "total_sales": len(self.sales) if hasattr(self, 'sales') else 0,
            "total_customers": len(self.customers) if hasattr(self, 'customers') else 0,
            "active_users": len(self.get_users_with_access()),
            "branches": self.get_config("branchConfig.currentBranches") or 0,
            "is_active": self.is_active,
            "is_main": self.is_main
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'objet en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "pharmacy_code": self.pharmacy_code,
            "license_number": self.license_number,
            "address": self.address,
            "city": self.city,
            "country": self.country,
            "phone": self.phone,
            "email": self.email,
            "is_active": self.is_active,
            "is_main": self.is_main,
            "config": self.config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
    
    # =========================
    # Représentation
    # =========================
    def __repr__(self):
        return f"<Pharmacy {self.name} ({self.license_number})>"
    
    def __str__(self):
        return f"{self.name} - {self.city}, {self.country}"