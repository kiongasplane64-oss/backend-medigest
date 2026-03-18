from pydantic import BaseModel, EmailStr, Field, validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
import re
from uuid import UUID


# ============================================
# SCHÉMAS DE CONFIGURATION (du nouveau fichier)
# ============================================

class CurrencyConfig(BaseModel):
    code: str = Field(..., min_length=3, max_length=3)
    symbol: str
    isActive: bool = True
    exchangeRate: float = Field(..., gt=0)


class DaysOffConfig(BaseModel):
    monday: bool = True
    tuesday: bool = True
    wednesday: bool = True
    thursday: bool = True
    friday: bool = True
    saturday: bool = True
    sunday: bool = False


class WorkingHoursConfig(BaseModel):
    enabled: bool = True
    startTime: str = Field(..., pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    endTime: str = Field(..., pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    overtimeEndTime: Optional[str] = Field(None, pattern="^([0-1]?[0-9]|2[0-3]):[0-5][0-9]$")
    daysOff: DaysOffConfig = DaysOffConfig()


class MarginConfig(BaseModel):
    defaultMargin: float = Field(..., ge=0, le=100)
    minMargin: float = Field(..., ge=0, le=100)
    maxMargin: float = Field(..., ge=0, le=100)
    
    @validator('maxMargin')
    def validate_margins(cls, v, values):
        if 'minMargin' in values and v < values['minMargin']:
            raise ValueError('maxMargin must be greater than minMargin')
        return v


class AutomaticPricingConfig(BaseModel):
    enabled: bool = False
    method: str = Field(..., pattern="^(percentage|coefficient|margin)$")
    value: float = Field(..., gt=0)


class BranchConfig(BaseModel):
    maxBranches: int = Field(..., ge=0)
    currentBranches: int = Field(..., ge=0)
    branches: List[Dict[str, Any]] = []


class ThemeConfig(BaseModel):
    theme: str = Field(..., pattern="^(light|dark|system)$")


class PharmacyInfoConfig(BaseModel):
    name: str
    address: str
    phone: str
    email: str
    licenseNumber: str
    logo: Optional[str] = None


# ============================================
# SCHÉMAS PHARMACIE DE BASE (fusion des deux)
# ============================================

class PharmacyBase(BaseModel):
    # Champs du nouveau fichier
    nom: Optional[str] = None
    # Champs de l'ancien fichier (avec noms anglais)
    name: Optional[str] = None
    license_number: str = Field(..., min_length=5)
    address: str
    city: str = "Kinshasa"
    country: str = "CD"
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: bool = True
    
    # Anciens champs supplémentaires
    opening_hours: Optional[Dict[str, str]] = None
    pharmacist_in_charge: Optional[str] = None
    pharmacist_license: Optional[str] = None
    
    # Configuration
    config: Optional[Dict[str, Any]] = None
    
    @validator('license_number')
    def validate_license_number(cls, v):
        if not v or len(v) < 5:
            raise ValueError("Le numéro de licence doit contenir au moins 5 caractères")
        # Validation supplémentaire pour le format selon le pays
        if not re.match(r'^[A-Z0-9-]+$', v):
            raise ValueError("Le numéro de licence ne peut contenir que des lettres majuscules, chiffres et tirets")
        return v
    
    @validator('phone')
    def validate_phone(cls, v):
        if v and not re.match(r'^\+?[0-9\s-]{8,}$', v):
            raise ValueError("Format de téléphone invalide")
        return v
    
    @validator('name', 'nom', pre=True, always=True)
    def set_name(cls, v, values):
        """Gère à la fois name et nom"""
        if v:
            return v
        if 'name' in values and values['name']:
            return values['name']
        return None


class PharmacyCreate(PharmacyBase):
    tenant_id: str  # Changé de int à str pour UUID
    
    # S'assurer qu'au moins un des deux noms est fourni
    @validator('name', 'nom', pre=True, always=True)
    def validate_name_exists(cls, v, values):
        if not v and not values.get('name') and not values.get('nom'):
            raise ValueError("Le nom de la pharmacie est requis")
        return v


class PharmacyUpdate(BaseModel):
    # Champs du nouveau fichier
    nom: Optional[str] = None
    # Champs de l'ancien fichier
    name: Optional[str] = None
    license_number: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    
    # Anciens champs supplémentaires
    opening_hours: Optional[Dict[str, str]] = None
    pharmacist_in_charge: Optional[str] = None
    pharmacist_license: Optional[str] = None
    
    # Configuration
    config: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE CONFIGURATION PHARMACIE
# ============================================

class PharmacyConfigUpdate(BaseModel):
    # Du nouveau fichier
    pharmacyInfo: Optional[PharmacyInfoConfig] = None
    currencies: Optional[List[CurrencyConfig]] = None
    primaryCurrency: Optional[str] = None
    taxRate: Optional[float] = Field(None, ge=0, le=100)
    lowStockThreshold: Optional[int] = Field(None, ge=0)
    expiryWarningDays: Optional[int] = Field(None, ge=0)
    allowNegativeStock: Optional[bool] = None
    workingHours: Optional[WorkingHoursConfig] = None
    productReturnDays: Optional[int] = Field(None, ge=0)
    marginConfig: Optional[MarginConfig] = None
    automaticPricing: Optional[AutomaticPricingConfig] = None
    theme: Optional[str] = Field(None, pattern="^(light|dark|system)$")
    initialCapital: Optional[float] = Field(None, ge=0)
    branchConfig: Optional[BranchConfig] = None
    
    # De l'ancien fichier (pour rétrocompatibilité)
    require_prescription: Optional[bool] = None
    enable_expiry_alerts: Optional[bool] = None
    low_stock_threshold: Optional[int] = None
    enable_barcode: Optional[bool] = None
    tax_rate: Optional[float] = None
    currency: Optional[str] = None
    language: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class PharmacyConfigResponse(BaseModel):
    pharmacy_id: str  # Changé de int à str pour UUID
    config: Dict[str, Any]
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS DE RÉPONSE (fusion des deux)
# ============================================

class PharmacyInDB(PharmacyBase):
    id: str  # Changé de int à str pour UUID
    tenant_id: str  # Changé de int à str pour UUID
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            UUID: lambda v: str(v),  # Conversion explicite en string
            datetime: lambda v: v.isoformat()
        }
    )


class PharmacyResponse(PharmacyInDB):
    # Champ calculé pour le nom unifié
    @property
    def display_name(self) -> str:
        return self.nom or self.name or "Pharmacie sans nom"
    
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            UUID: lambda v: str(v),  # Conversion explicite en string
            datetime: lambda v: v.isoformat()
        }
    )
    
    def dict(self, *args, **kwargs):
        """Surcharge pour garantir que les UUIDs sont des strings"""
        d = super().dict(*args, **kwargs)
        # Conversion explicite des UUIDs en strings
        if 'id' in d and d['id'] and not isinstance(d['id'], str):
            d['id'] = str(d['id'])
        if 'tenant_id' in d and d['tenant_id'] and not isinstance(d['tenant_id'], str):
            d['tenant_id'] = str(d['tenant_id'])
        return d
    
    def model_dump(self, *args, **kwargs):
        """Pour Pydantic v2"""
        d = super().model_dump(*args, **kwargs)
        # Conversion explicite des UUIDs en strings
        if 'id' in d and d['id'] and not isinstance(d['id'], str):
            d['id'] = str(d['id'])
        if 'tenant_id' in d and d['tenant_id'] and not isinstance(d['tenant_id'], str):
            d['tenant_id'] = str(d['tenant_id'])
        return d


# ============================================
# SCHÉMAS SPÉCIFIQUES POUR LES LIMITES
# ============================================

class PharmacyLimitsResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    current_plan: str
    limits: Dict[str, Any]
    current_pharmacies_count: int
    max_pharmacies_allowed: int
    remaining_pharmacies: int
    can_create_more: bool
    max_branches_per_pharmacy: int
    
    model_config = ConfigDict(from_attributes=True)


class ServiceStatusResponse(BaseModel):
    in_service: bool
    restrictions_enabled: bool
    current_time_utc: str
    current_day: str
    is_working_day: bool
    is_within_hours: bool
    working_hours: Dict[str, Optional[str]]
    message: str
    next_service_time: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class BranchCreateResponse(BaseModel):
    message: str
    branch: Dict[str, Any]
    remaining: int
    current_branches: int
    max_allowed: int
    
    model_config = ConfigDict(from_attributes=True)


# ============================================
# SCHÉMAS POUR LES UTILISATEURS EN LIGNE
# ============================================

class OnlineUserResponse(BaseModel):
    id: str
    nom_complet: Optional[str] = None
    email: str
    role: str
    last_login: Optional[str] = None
    login_duration: str
    status: str = "online"
    
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            UUID: lambda v: str(v)
        }
    )


class OnlineUsersResponse(BaseModel):
    pharmacy_id: str  # Changé de int à str pour UUID
    pharmacy_name: str
    online_count: int
    users: List[OnlineUserResponse]
    timestamp: str
    
    model_config = ConfigDict(from_attributes=True)


class PricingConfig(AutomaticPricingConfig):
    """Alias pour compatibilité avec l'ancien code"""
    pass