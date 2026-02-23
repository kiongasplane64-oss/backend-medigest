# app/schemas/stock.py
from pydantic import BaseModel, Field, validator, model_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum

class ProductType(str, Enum):
    MEDICAMENT = "medicament"
    PARAPHARMACIE = "parapharmacie"
    MATERIEL = "materiel"
    AUTRE = "autre"

class StockStatus(str, Enum):
    NORMAL = "normal"
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    OVER_STOCK = "over_stock"

class ExpiryStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

class ProductBase(BaseModel):
    """Schéma de base pour un produit"""
    code: str = Field(..., min_length=1, max_length=50, description="Code interne")
    barcode: Optional[str] = Field(None, max_length=100, description="Code-barres")
    name: str = Field(..., min_length=2, max_length=200, description="Nom du produit")
    commercial_name: Optional[str] = Field(None, max_length=200)
    
    # Description
    description: Optional[str] = None
    active_ingredient: Optional[str] = Field(None, max_length=200)
    dosage: Optional[str] = Field(None, max_length=100)
    galenic_form: Optional[str] = Field(None, max_length=100)
    laboratory: Optional[str] = Field(None, max_length=200)
    
    # Classification
    category: Optional[str] = Field(None, max_length=100)
    subcategory: Optional[str] = Field(None, max_length=100)
    therapeutic_class: Optional[str] = Field(None, max_length=200)
    product_type: ProductType = Field(default=ProductType.MEDICAMENT)
    
    # Stock
    quantity: int = Field(default=0, ge=0)
    unit: str = Field(default="unité", max_length=50)
    alert_threshold: int = Field(default=10, ge=0)
    minimum_stock: int = Field(default=5, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)
    
    # Prix
    purchase_price: float = Field(..., gt=0, description="Prix d'achat")
    selling_price: float = Field(..., gt=0, description="Prix de vente")
    wholesale_price: Optional[float] = Field(None, gt=0)
    tva_rate: float = Field(default=0.0, ge=0, le=100)
    
    # Péremption
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    authorization_number: Optional[str] = Field(None, max_length=100)
    
    # Réglementation
    packaging: Optional[str] = Field(None, max_length=100)
    dci: Optional[str] = Field(None, max_length=200)
    prescription_required: bool = Field(default=False)
    regulatory_class: Optional[str] = Field(None, max_length=50)
    
    # Gestion
    main_supplier: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)
    
    # Métadonnées
    image_url: Optional[str] = Field(None, max_length=500)
    leaflet_url: Optional[str] = Field(None, max_length=500)

class ProductCreate(ProductBase):
    """Création d'un nouveau produit"""
    
    @validator('expiry_date')
    def validate_expiry_date(cls, v):
        if v and v < date.today():
            raise ValueError('La date de péremption ne peut pas être dans le passé')
        return v
    
    @model_validator(mode="after")
    def validate_all_constraints(cls, values):  # Validation combinée
        # Validation des prix
        selling_price = values.selling_price
        purchase_price = values.purchase_price
        if selling_price <= purchase_price:
            raise ValueError('Le prix de vente doit être supérieur au prix d\'achat')
        
        # Validation des limites de stock
        minimum = values.minimum_stock
        maximum = values.maximum_stock
        if maximum is not None and minimum > maximum:
            raise ValueError('Le stock minimum ne peut pas être supérieur au stock maximum')
        
        return values
    
class ProductUpdate(BaseModel):
    """Mise à jour d'un produit"""
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = None
    category: Optional[str] = Field(None, max_length=100)
    
    # Prix
    purchase_price: Optional[float] = Field(None, gt=0)
    selling_price: Optional[float] = Field(None, gt=0)
    wholesale_price: Optional[float] = Field(None, gt=0)
    
    # Stock
    quantity: Optional[int] = Field(None, ge=0)
    alert_threshold: Optional[int] = Field(None, ge=0)
    minimum_stock: Optional[int] = Field(None, ge=0)
    maximum_stock: Optional[int] = Field(None, ge=0)
    
    # Péremption
    expiry_date: Optional[date] = None
    batch_number: Optional[str] = Field(None, max_length=100)
    
    # Autres
    main_supplier: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    
    @model_validator(mode="after")
    def validate_prices(cls, values):
        selling_price = values.get('selling_price')
        purchase_price = values.get('purchase_price')
        if selling_price is not None and purchase_price is not None:
            if selling_price <= purchase_price:
                raise ValueError('Le prix de vente doit être supérieur au prix d\'achat')
        return values

class ProductInDB(ProductBase):
    """Produit tel que stocké en base de données"""
    id: UUID
    tenant_id: UUID
    
    # Statuts calculés
    available_quantity: int
    reserved_quantity: int = 0
    stock_status: StockStatus
    expiry_status: ExpiryStatus
    
    # Valeurs calculées
    purchase_value: float
    selling_value: float
    margin_total: float
    margin_rate: float
    
    # Historique
    total_sold: int = 0
    total_purchased: int = 0
    last_sale_date: Optional[datetime] = None
    last_purchase_date: Optional[datetime] = None
    
    # Métadonnées
    is_active: bool = True
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ProductResponse(BaseModel):
    """Réponse API pour les opérations sur les produits"""
    message: str
    product: ProductInDB

class ProductListResponse(BaseModel):
    """Réponse pour la liste des produits"""
    total: int
    page: int
    limit: int
    products: List[ProductInDB]
    summary: Dict[str, Any] = Field(
        default_factory=lambda: {
            "total_products": 0,
            "total_value_purchase": 0.0,
            "total_value_selling": 0.0,
            "out_of_stock": 0,
            "low_stock": 0,
            "expired_soon": 0
        }
    )

class StockAdjustment(BaseModel):
    """Ajustement de stock"""
    product_id: UUID
    new_quantity: int = Field(..., ge=0)
    reason: str = Field(..., max_length=200)
    notes: Optional[str] = None

class StockMovementFilter(BaseModel):
    """Filtres pour les mouvements de stock"""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    product_id: Optional[UUID] = None
    movement_type: Optional[str] = None
    created_by: Optional[UUID] = None

class InventoryCountRequest(BaseModel):
    """Demande de comptage d'inventaire"""
    product_id: UUID
    counted_quantity: int = Field(..., ge=0)
    notes: Optional[str] = None

class ProductSearch(BaseModel):
    """Recherche de produits"""
    query: Optional[str] = None
    category: Optional[str] = None
    supplier: Optional[str] = None
    stock_status: Optional[StockStatus] = None
    expiry_status: Optional[ExpiryStatus] = None
    barcode: Optional[str] = None
    code: Optional[str] = None

class StockStats(BaseModel):
    """Statistiques de stock"""
    total_products: int
    total_items: int
    total_purchase_value: float
    total_selling_value: float
    average_margin_rate: float
    out_of_stock_count: int
    low_stock_count: int
    expired_count: int
    expiring_soon_count: int
    category_distribution: Dict[str, int]
    value_by_category: Dict[str, float]

class ProductMergeRequest(BaseModel):
    """Demande de fusion de produits"""
    product_ids: List[UUID] = Field(..., min_items=2)
    merge_strategy: str = Field(
        default="average",
        pattern="^(average|max|min|first)$",
        description="Stratégie de fusion des prix"
    )
    expiry_strategy: str = Field(
        default="most_recent",
        pattern="^(most_recent|most_ancient|none)$",
        description="Stratégie de fusion des dates de péremption"
    )
    keep_product_id: UUID = Field(description="ID du produit à conserver")

class ExportFormat(str, Enum):
    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"
