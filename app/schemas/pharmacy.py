from pydantic import BaseModel, EmailStr, validator
from typing import Optional, Dict, Any
from datetime import datetime

class PharmacyBase(BaseModel):
    name: str
    license_number: str
    address: str
    city: str
    country: str = "Senegal"
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: bool = True
    opening_hours: Optional[Dict[str, str]] = None
    pharmacist_in_charge: Optional[str] = None
    pharmacist_license: Optional[str] = None
    config: Optional[Dict[str, Any]] = None

class PharmacyCreate(PharmacyBase):
    tenant_id: int
    
    @validator('license_number')
    def validate_license_number(cls, v):
        if not v or len(v) < 5:
            raise ValueError("Le numéro de licence doit contenir au moins 5 caractères")
        return v

class PharmacyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    opening_hours: Optional[Dict[str, str]] = None
    config: Optional[Dict[str, Any]] = None

class PharmacyInDB(PharmacyBase):
    id: int
    tenant_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class PharmacyResponse(PharmacyInDB):
    pass

class PharmacyConfigUpdate(BaseModel):
    require_prescription: Optional[bool] = None
    enable_expiry_alerts: Optional[bool] = None
    low_stock_threshold: Optional[int] = None
    enable_barcode: Optional[bool] = None
    tax_rate: Optional[float] = None
    currency: Optional[str] = None
    language: Optional[str] = None