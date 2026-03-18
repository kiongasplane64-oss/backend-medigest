# app/models/pharmacy.py
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship, Session, validates
from datetime import datetime
from app.db.base import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid
import json
from typing import Optional, Dict, Any, List
import pytz


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
    
    # Informations de licence
    license_number = Column(String(100), nullable=False)
    
    # Localisation
    address = Column(Text, nullable=False)
    city = Column(String(100), nullable=False)
    country = Column(String(2), nullable=False, default="CD")  # Code ISO 2 lettres
    phone = Column(String(50), nullable=True)
    email = Column(String(255), nullable=True)
    
    # =========================
    # Statut & Spécialisation
    # =========================
    is_active = Column(Boolean, default=True)
    is_main = Column(Boolean, default=False, comment="Pharmacie principale du tenant")
    
    # Pharmacien responsable (pour compatibilité)
    pharmacist_in_charge = Column(String(255), nullable=True)
    pharmacist_license = Column(String(100), nullable=True)
    
    # =========================
    # CONFIGURATION COMPLÈTE (harmonisée avec les schémas)
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
        
        # Fiscalité
        "taxRate": 16.0,
        
        # Stock et alertes
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        
        # Heures de service (avec fuseau horaire)
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",
            "endTime": "20:00",
            "overtimeEndTime": "22:00",
            "timezone": "Africa/Kinshasa",
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
        
        # Configuration des prix et marges
        "marginConfig": {
            "defaultMargin": 25.0,
            "minMargin": 10.0,
            "maxMargin": 50.0
        },
        "automaticPricing": {
            "enabled": False,
            "method": "percentage",
            "value": 25.0
        },
        
        # Thème et apparence
        "theme": "system",
        
        # Capital et finances
        "initialCapital": 0.0,
        
        # Configuration des branches/succursales
        "branchConfig": {
            "maxBranches": 1,
            "currentBranches": 0,
            "branches": []
        },
        
        # Métadonnées
        "createdAt": None,
        "updatedAt": None
    })
    
    # Métadonnées additionnelles (pour compatibilité)
    meta_data = Column(JSON, default=lambda: {})
    
    # Pour compatibilité avec ancien code
    opening_hours = Column(JSON, nullable=True)
    pharmacy_code = Column(String(50), nullable=True)
    website = Column(String(255), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    pharmacy_type = Column(String(50), default="retail")
    license_issuing_authority = Column(String(255), nullable=True)
    license_expiry_date = Column(DateTime, nullable=True)
    pharmacist_contact = Column(String(50), nullable=True)
    
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
        if phone and not phone.replace('+', '').replace(' ', '').replace('-', '').isdigit():
            raise ValueError("Numéro de téléphone invalide")
        return phone
    
    @validates('country')
    def validate_country(self, key, country):
        if country and len(country) != 2:
            raise ValueError("Le code pays doit être sur 2 caractères (ISO 3166-1 alpha-2)")
        return country.upper() if country else country
    
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
    # Gestion des heures de service (avec fuseau horaire)
    # =========================
    def get_timezone(self) -> pytz.timezone:
        """Récupère l'objet timezone pour la pharmacie"""
        timezone_str = self.get_config("workingHours.timezone") or "Africa/Kinshasa"
        try:
            return pytz.timezone(timezone_str)
        except:
            return pytz.timezone("Africa/Kinshasa")
    
    def get_current_time_in_pharmacy_tz(self) -> datetime:
        """Retourne l'heure actuelle dans le fuseau de la pharmacie"""
        return datetime.now(self.get_timezone())
    
    def convert_to_pharmacy_time(self, dt: datetime) -> datetime:
        """Convertit une datetime UTC vers le fuseau de la pharmacie"""
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt.astimezone(self.get_timezone())
    
    def convert_from_pharmacy_time(self, dt: datetime) -> datetime:
        """Convertit une datetime du fuseau pharmacie vers UTC"""
        if dt.tzinfo is None:
            # Si pas de timezone, on suppose que c'est en heure pharmacie
            dt = self.get_timezone().localize(dt)
        return dt.astimezone(pytz.UTC)
    
    def is_in_service(self, check_time: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Vérifie si la pharmacie est en service à l'heure donnée
        Utilise le fuseau horaire configuré pour la pharmacie
        """
        working_hours = self.get_config("workingHours") or {}
        
        if not working_hours.get("enabled", True):
            return {
                "in_service": True,
                "restrictions_enabled": False,
                "message": "Service toujours disponible (pas de restriction horaire)",
                "timezone": working_hours.get("timezone", "Africa/Kinshasa")
            }
        
        # Utiliser le fuseau horaire de la pharmacie
        pharmacy_tz = self.get_timezone()
        
        if check_time is None:
            check_time = datetime.now(pharmacy_tz)
        else:
            # Si check_time est fourni sans timezone, on suppose UTC
            if check_time.tzinfo is None:
                check_time = pytz.UTC.localize(check_time)
            check_time = check_time.astimezone(pharmacy_tz)
        
        now_utc = datetime.now(pytz.UTC)
        
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
        
        # Calculer le prochain service
        next_service_time = None
        if not in_service and is_working_day:
            # Prochain service aujourd'hui (si après fermeture)
            if current_time > end_minutes:
                # Demain
                next_service_time = f"{working_hours.get('startTime')} (demain)"
            # Prochain service aujourd'hui (si avant ouverture)
            elif current_time < start_minutes:
                next_service_time = f"{working_hours.get('startTime')} (aujourd'hui)"
        elif not in_service and not is_working_day:
            # Trouver le prochain jour ouvré
            days_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
            current_index = days_order.index(current_day) if current_day in days_order else 0
            
            for i in range(1, 8):
                next_index = (current_index + i) % 7
                next_day = days_order[next_index]
                if days_off.get(next_day, False):
                    next_service_time = f"{working_hours.get('startTime')} {next_day}"
                    break
        
        return {
            "in_service": in_service,
            "restrictions_enabled": True,
            "current_time_utc": now_utc.isoformat(),
            "current_time_local": check_time.isoformat(),
            "timezone": str(pharmacy_tz),
            "current_day": current_day,
            "is_working_day": is_working_day,
            "is_within_hours": is_within_hours,
            "working_hours": {
                "start": working_hours.get("startTime"),
                "end": working_hours.get("endTime"),
                "overtime": working_hours.get("overtimeEndTime")
            },
            "message": "En service" if in_service else "Hors service",
            "next_service_time": next_service_time
        }
    
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
            "is_active": True
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
        
        from_rate = None
        to_rate = None
        
        for c in currencies:
            if c["code"] == from_currency:
                from_rate = c["exchangeRate"]
            if c["code"] == to_currency:
                to_rate = c["exchangeRate"]
        
        if not from_rate or not to_rate:
            raise ValueError(f"Devise non trouvée: {from_currency if not from_rate else to_currency}")
        
        amount_in_usd = amount / from_rate if from_currency != "USD" else amount
        converted = amount_in_usd * to_rate if to_currency != "USD" else amount_in_usd
        
        return round(converted, 2)
    
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
            "is_main": self.is_main,
            "timezone": self.get_config("workingHours.timezone") or "Africa/Kinshasa"
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'objet en dictionnaire"""
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "license_number": self.license_number,
            "address": self.address,
            "city": self.city,
            "country": self.country,
            "phone": self.phone,
            "email": self.email,
            "is_active": self.is_active,
            "is_main": self.is_main,
            "pharmacist_in_charge": self.pharmacist_in_charge,
            "pharmacist_license": self.pharmacist_license,
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