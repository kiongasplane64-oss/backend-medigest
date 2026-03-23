# app/schemas/sale.py
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, date
from uuid import UUID
from enum import Enum
from decimal import Decimal


# ============================
# ENUMS
# ============================
class SaleStatus(str, Enum):
    DRAFT = "draft"
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class PaymentMethod(str, Enum):
    CASH = "cash"
    MOBILE_MONEY = "mobile_money"
    CARD = "card"
    CHECK = "check"
    BANK_TRANSFER = "bank_transfer"
    CREDIT = "credit"


# ============================
# SALE ITEMS
# ============================
class SaleItemBase(BaseModel):
    product_id: UUID
    quantity: int = Field(..., gt=0, description="Quantité du produit")
    unit_price: Decimal = Field(..., gt=0, max_digits=12, decimal_places=2, description="Prix unitaire")
    discount_percent: Decimal = Field(Decimal('0.00'), ge=0, le=100, max_digits=5, decimal_places=2, description="Pourcentage de remise")
    tva_rate: Decimal = Field(Decimal('0.00'), ge=0, le=100, max_digits=5, decimal_places=2, description="Taux de TVA")
    batch_number: Optional[str] = Field(None, max_length=100, description="Numéro de lot")
    expiry_date: Optional[date] = Field(None, description="Date de péremption")
    
    @field_validator('expiry_date')
    def validate_expiry_date(cls, v):
        if v and v < date.today():
            raise ValueError('La date de péremption ne peut pas être dans le passé')
        return v


class SaleItemCreate(SaleItemBase):
    model_config = ConfigDict(from_attributes=True)


class SaleItemResponse(SaleItemBase):
    id: UUID
    sale_id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    product_code: str
    product_name: str
    subtotal: Decimal
    discount_amount: Decimal
    tva_amount: Decimal
    total: Decimal
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


# ============================
# SALE CREATE
# ============================
class SaleCreate(BaseModel):
    pharmacy_id: Optional[UUID] = Field(None, description="ID de la pharmacie (optionnel, utilise la pharmacie par défaut)")
    client_id: Optional[UUID] = None
    client_name: Optional[str] = Field("Client Générique", max_length=100)
    client_phone: Optional[str] = Field(None, max_length=20)
    payment_method: PaymentMethod
    reference_payment: Optional[str] = Field(None, max_length=100, description="Référence du paiement (numéro de chèque, transaction, etc.)")
    is_credit: bool = False
    credit_due_date: Optional[date] = None
    guarantee_deposit: Decimal = Field(Decimal('0.00'), ge=0, max_digits=15, decimal_places=2)
    guarantor_name: Optional[str] = Field(None, max_length=100)
    guarantor_phone: Optional[str] = Field(None, max_length=20)
    global_discount: Decimal = Field(Decimal('0.00'), ge=0, le=100, max_digits=5, decimal_places=2)
    notes: Optional[str] = None
    invoice_number: Optional[str] = Field(None, max_length=50)
    items: List[SaleItemCreate]
    
    @model_validator(mode='after')
    def validate_credit_sale(self):
        if self.is_credit:
            if not self.credit_due_date:
                raise ValueError('credit_due_date est requis pour les ventes à crédit')
            if self.credit_due_date < date.today():
                raise ValueError('La date d\'échéance ne peut pas être dans le passé')
        return self
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('La vente doit contenir au moins un article')
        return v


# ============================
# SALE UPDATE
# ============================
class SaleUpdate(BaseModel):
    status: Optional[SaleStatus] = None
    notes: Optional[str] = None
    cancel_reason: Optional[str] = None
    refund_amount: Optional[Decimal] = Field(None, ge=0, max_digits=15, decimal_places=2)


# ============================
# SALE FILTER
# ============================
class SaleFilter(BaseModel):
    pharmacy_id: Optional[UUID] = Field(None, description="Filtrer par pharmacie spécifique")
    status: Optional[SaleStatus] = None
    payment_method: Optional[PaymentMethod] = None
    is_credit: Optional[bool] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    client_id: Optional[UUID] = None
    seller_id: Optional[UUID] = None
    search: Optional[str] = None
    
    @model_validator(mode='after')
    def validate_date_range(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError('start_date ne peut pas être après end_date')
        return self


# ============================
# DAILY STATS RESPONSE (AJOUTÉ)
# ============================
class TopProductStats(BaseModel):
    """Statistiques d'un produit en top"""
    product: str
    quantity: int
    amount: float


class PharmacyDailyStats(BaseModel):
    """Statistiques par pharmacie pour une journée"""
    pharmacy_id: str
    pharmacy_name: str
    sales_count: int
    total_amount: float
    percentage: float


class DailyStatsResponse(BaseModel):
    """Réponse pour les statistiques quotidiennes"""
    date: str
    sales_count: int
    total_amount: float
    average_basket: float
    items_sold: int
    top_products: List[TopProductStats]
    by_pharmacy: List[PharmacyDailyStats]


# ============================
# SALE RESPONSE / IN DB
# ============================
class SaleInDB(BaseModel):
    id: UUID
    tenant_id: UUID
    pharmacy_id: UUID
    pharmacy_name: Optional[str] = None
    pharmacy_code: Optional[str] = None
    reference: str
    client_id: Optional[UUID]
    client_name: str
    client_phone: Optional[str]
    created_by: UUID
    seller_name: str
    payment_method: str
    reference_payment: Optional[str]
    payment_date: Optional[datetime]
    is_credit: bool
    credit_due_date: Optional[date]
    guarantee_deposit: Decimal
    guarantor_name: Optional[str]
    guarantor_phone: Optional[str]
    global_discount: Decimal
    notes: Optional[str]
    subtotal: Decimal
    total_discount: Decimal
    total_tva: Decimal
    total_amount: Decimal
    amount_paid: Decimal
    amount_due: Decimal
    status: str
    invoice_number: Optional[str]
    invoice_path: Optional[str]
    receipt_path: Optional[str]
    created_at: datetime
    updated_at: datetime
    validated_at: Optional[datetime]
    validated_by: Optional[UUID]
    cancelled_at: Optional[datetime]
    cancelled_by: Optional[UUID]
    cancel_reason: Optional[str]
    
    # Propriétés calculées
    is_paid: bool
    credit_status: str
    days_overdue: int
    
    model_config = ConfigDict(from_attributes=True)


class SaleResponse(BaseModel):
    message: str
    sale: SaleInDB
    pharmacy: Optional[Dict[str, Any]] = None
    alerts: Optional[List[Dict[str, Any]]] = None
    receipt_available: bool = False
    receipt_url: Optional[str] = None


# ============================
# SALE LIST RESPONSE
# ============================
class SaleListResponse(BaseModel):
    items: List[SaleInDB]
    total: int
    page: int
    size: int
    has_more: bool
    pharmacies_summary: Optional[Dict[str, Any]] = None


# ============================
# SALE DETAIL RESPONSE
# ============================
class SaleDetailResponse(BaseModel):
    sale: SaleInDB
    items: List[SaleItemResponse]
    pharmacy: Dict[str, Any]
    client: Optional[Dict[str, Any]] = None
    creator: Dict[str, Any]
    payments: List[Dict[str, Any]] = []
    refunds: List[Dict[str, Any]] = []
    can_refund: bool
    can_cancel: bool
    can_validate: bool


# ============================
# SALE STATISTICS
# ============================
class DailyStats(BaseModel):
    date: date
    sales_count: int
    total_amount: float
    average_basket: float
    items_sold: int
    top_products: List[Dict[str, Any]]
    by_payment_method: Dict[str, float]


class PharmacyStats(BaseModel):
    pharmacy_id: UUID
    pharmacy_name: str
    pharmacy_code: str
    is_main: bool
    total_sales: int
    total_amount: float
    average_basket: float
    items_sold: int
    percentage_of_total: float


class PeriodStats(BaseModel):
    """Statistiques pour une période"""
    total: float
    count: int
    average: float


class SalesStatsResponse(BaseModel):
    """Réponse pour les statistiques globales des ventes"""
    today: DailyStatsResponse
    week: PeriodStats
    month: PeriodStats
    year: PeriodStats


# ============================
# QUICK SALE
# ============================
class QuickSaleItem(BaseModel):
    product_id: UUID
    quantity: int = Field(..., gt=0)
    unit_price: Optional[Decimal] = None
    
    model_config = ConfigDict(from_attributes=True)


class QuickSaleRequest(BaseModel):
    items: List[QuickSaleItem]
    payment_method: PaymentMethod
    client_name: Optional[str] = "Client Générique"
    pharmacy_id: Optional[UUID] = None
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('La vente rapide doit contenir au moins un article')
        return v


# ============================
# CREDIT SALE
# ============================
class CreditSaleCreate(SaleCreate):
    is_credit: bool = True
    credit_due_date: date
    guarantee_deposit: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    guarantor_name: str = Field(..., max_length=100)
    guarantor_phone: str = Field(..., max_length=20)
    
    @field_validator('credit_due_date')
    def validate_credit_due_date(cls, v):
        if v < date.today():
            raise ValueError('La date d\'échéance doit être dans le futur')
        return v


# ============================
# REFUND
# ============================
class RefundItem(BaseModel):
    sale_item_id: UUID
    quantity: int = Field(..., gt=0)
    reason: str


class SaleRefundRequest(BaseModel):
    sale_id: UUID
    items: List[RefundItem]
    refund_amount: Decimal = Field(..., gt=0, max_digits=15, decimal_places=2)
    refund_reason: str
    refund_method: PaymentMethod
    
    @field_validator('items')
    def validate_items(cls, v):
        if not v or len(v) == 0:
            raise ValueError('Le remboursement doit concerner au moins un article')
        return v


# ============================
# RECEIPT DATA
# ============================
class ReceiptData(BaseModel):
    sale_id: UUID
    include_logo: bool = True
    include_qrcode: bool = True
    include_pharmacy_info: bool = True
    include_tax_details: bool = True
    additional_notes: Optional[str] = None
    language: str = "fr"


# ============================
# PHARMACY CONTEXT
# ============================
class PharmacyContext(BaseModel):
    id: UUID
    name: str
    code: str
    address: str
    phone: str
    is_main: bool
    is_active: bool


class UserPharmacyAccess(BaseModel):
    accessible_pharmacies: List[PharmacyContext]
    current_pharmacy: Optional[PharmacyContext] = None
    can_switch: bool


# ============================
# VALIDATION
# ============================
class SaleValidationRequest(BaseModel):
    sale_id: UUID
    validator_notes: Optional[str] = None
    force_approval: bool = False


# ============================
# EXPORT
# ============================
class SaleExportRequest(BaseModel):
    start_date: date
    end_date: date
    pharmacy_id: Optional[UUID] = None
    format: str = Field("xlsx", pattern="^(xlsx|csv|pdf)$")
    include_details: bool = False
    
    @model_validator(mode='after')
    def validate_date_range(self):
        if self.start_date > self.end_date:
            raise ValueError('start_date ne peut pas être après end_date')
        if (self.end_date - self.start_date).days > 365:
            raise ValueError('La période ne peut pas dépasser 365 jours')
        return self


class SaleExportResponse(BaseModel):
    filename: str
    download_url: str
    record_count: int
    file_size: str
    generated_at: datetime


# ============================
# SALE IMPACT RESPONSE
# ============================
class SaleImpactResponse(BaseModel):
    """Réponse pour l'impact des ventes sur le stock"""
    product_id: UUID
    product_code: str
    product_name: str
    unit: str
    total_sold: int
    total_revenue: float
    sale_count: int
    average_price: float
    stock_impact: int  # Impact négatif sur le stock


# ============================
# PERIOD STATS (pour compatibilité)
# ============================
class PeriodStatsResponse(BaseModel):
    """Statistiques pour une période (compatibilité)"""
    total: float
    count: int
    average: float