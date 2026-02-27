# app/models/product.py
import uuid
from datetime import datetime, date
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, Date, Text, ForeignKey, Numeric, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, validates
from sqlalchemy.sql import func
from sqlalchemy import Computed
from app.db.base import Base
from sqlalchemy.ext.hybrid import hybrid_property

class Product(Base):
    """
    Modèle représentant un produit dans le stock de la pharmacie.
    Gère les informations de stock, prix, péremption, etc.
    """
    __tablename__ = "products"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, index=True)
    pharmacy_id = Column(UUID(as_uuid=True), ForeignKey('pharmacies.id'), nullable=False, index=True)
    
    
    # =====================================
    # IDENTIFICATION DU PRODUIT
    # =====================================
    code = Column(String(50), nullable=True, index=True, comment="Code interne")
    barcode = Column(String(100), nullable=True, index=True, comment="Code-barres")
    name = Column(String(200), nullable=False, index=True)
    commercial_name = Column(String(200), nullable=True)
    
    # =====================================
    # DESCRIPTION ET COMPOSITION
    # =====================================
    description = Column(Text, nullable=True)
    active_ingredient = Column(String(200), nullable=True)
    dosage = Column(String(100), nullable=True)
    galenic_form = Column(String(100), nullable=True, comment="Comprimé, sirop, injectable, etc.")
    laboratory = Column(String(200), nullable=True)
    dci = Column(String(200), nullable=True, comment="Dénomination Commune Internationale")
    
    # =====================================
    # CLASSIFICATION
    # =====================================
    category = Column(String(100), nullable=True, index=True)
    subcategory = Column(String(100), nullable=True)
    therapeutic_class = Column(String(200), nullable=True)
    product_type = Column(String(30), default="medicament", 
                         comment="medicament, parapharmacie, materiel, autre")
    
    # =====================================
    # GESTION DU STOCK
    # =====================================
    quantity = Column(Integer, default=0, nullable=False)
    available_quantity = Column(Integer, default=0, nullable=False)
    reserved_quantity = Column(Integer, default=0, nullable=False)
    unit = Column(String(50), default="unité")
    
    # Seuils d'alerte
    alert_threshold = Column(Integer, default=10, nullable=False)
    minimum_stock = Column(Integer, default=5, nullable=False)
    maximum_stock = Column(Integer, nullable=True)
    
    # Localisation
    location = Column(String(100), nullable=True, comment="Emplacement physique")
    
    # =====================================
    # PRIX ET FINANCES
    # =====================================
    purchase_price = Column(Numeric(12, 2), nullable=False, default=0.0)
    selling_price = Column(Numeric(12, 2), nullable=False, default=0.0)
    wholesale_price = Column(Numeric(12, 2), nullable=True)
    
    # Taxes
    tva_rate = Column(Numeric(5, 2), default=0.0, comment="Taux TVA en %")
    has_tva = Column(Boolean, default=False)
    
    # Marges calculées
    margin_amount = Column(
    Numeric(12, 2),
    Computed("selling_price - purchase_price", persisted=True)
    )

    margin_rate = Column(
        Numeric(5, 2),
        Computed(
            "((selling_price - purchase_price) / NULLIF(purchase_price, 0)) * 100",
            persisted=True
        )
    )

    # =====================================
    # PÉREMPTION ET LOTS
    # =====================================
    expiry_date = Column(Date, nullable=True, index=True)
    batch_number = Column(String(100), nullable=True)
    authorization_number = Column(String(100), nullable=True)
    
    # =====================================
    # RÉGLEMENTATION
    # =====================================
    packaging = Column(String(100), nullable=True)
    prescription_required = Column(Boolean, default=False)
    regulatory_class = Column(String(50), nullable=True, 
                            comment="Classe réglementaire: A, B, C, etc.")
    
    # =====================================
    # FOURNISSEURS
    # =====================================
    main_supplier = Column(String(200), nullable=True)
    supplier_code = Column(String(100), nullable=True)
    supplier_price = Column(Numeric(12, 2), nullable=True)
    
    # =====================================
    # MÉTADONNÉES ET MÉDIAS
    # =====================================
    image_url = Column(String(500), nullable=True)
    leaflet_url = Column(String(500), nullable=True)
    notes = Column(Text, nullable=True)
    meta_data = Column(Text, nullable=True, comment="Métadonnées JSON supplémentaires")
    
    # =====================================
    # STATUT ET FLAGS
    # =====================================
    is_active = Column(Boolean, default=True, index=True)
    is_available = Column(Boolean, default=True, index=True)
    is_discounted = Column(Boolean, default=False)
    stock_status = Column(String(20), default="normal", 
                         comment="normal, low_stock, out_of_stock, over_stock")
    expiry_status = Column(String(20), default="unknown", 
                          comment="ok, warning, critical, expired, unknown")
    
    # =====================================
    # STATISTIQUES
    # =====================================
    total_sold = Column(Integer, default=0)
    total_purchased = Column(Integer, default=0)
    last_sale_date = Column(DateTime, nullable=True)
    last_purchase_date = Column(DateTime, nullable=True)
    last_adjustment_date = Column(DateTime, nullable=True)
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    deleted_at = Column(DateTime, nullable=True)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship(
        "Tenant",
        back_populates="products"
    )
    pharmacy = relationship("Pharmacy", back_populates="products")
    

    #stock_movements = relationship("StockMovement", back_populates="product", cascade="all, delete-orphan")
    #physical_inventory_items = relationship("PhysicalInventoryItem", back_populates="product")
    product_stocks = relationship(
        "ProductStock",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    
    stock_movements = relationship(
        "StockMovement",
        back_populates="product",
        cascade="all, delete-orphan",
        lazy="selectin"
    )
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_products_name', 'name'),
        Index('ix_products_code', 'code'),
        Index('ix_products_barcode', 'barcode'),
        Index('ix_products_category', 'category'),
        Index('ix_products_expiry_date', 'expiry_date'),
        Index('ix_products_stock_status', 'stock_status'),
        Index('ix_products_expiry_status', 'expiry_status'),
        Index('ix_products_tenant_active', 'tenant_id', 'is_active'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('quantity', 'available_quantity')
    def validate_quantity(self, key, value):
        """Valide que la quantité est positive"""
        if value < 0:
            raise ValueError(f"La {key} ne peut pas être négative")
        return value
    
    @validates('purchase_price', 'selling_price')
    def validate_price(self, key, value):
        """Valide que le prix est positif"""
        if value is not None and value < 0:
            raise ValueError(f"Le {key} ne peut pas être négatif")
        return value
    
    @validates('expiry_date')
    def validate_expiry_date(self, key, value):
        """Valide la date de péremption"""
        if value and value < date.today():
            raise ValueError("La date de péremption ne peut pas être dans le passé lors de la création")
        return value
    
    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def purchase_value(self):
        """Valeur d'achat totale du stock"""
        return float(self.quantity * self.purchase_price)
    
    @hybrid_property
    def selling_value(self):
        """Valeur de vente totale du stock"""
        return float(self.quantity * self.selling_price)
    
    @hybrid_property
    def total_margin(self):
        """Marge totale du stock"""
        return float(self.quantity * self.margin_amount)
    
    @hybrid_property
    def days_until_expiry(self):
        """Jours restants avant péremption"""
        if not self.expiry_date:
            return None
        today = date.today()
        return (self.expiry_date - today).days
    
    @hybrid_property
    def is_expired(self):
        """Vérifie si le produit est périmé"""
        if not self.expiry_date:
            return False
        return self.expiry_date < date.today()
    
    @hybrid_property
    def is_expiring_soon(self):
        """Vérifie si le produit expire bientôt (<= 30 jours)"""
        if not self.expiry_date or self.is_expired:
            return False
        return self.days_until_expiry <= 30
    
    @hybrid_property
    def is_critical_expiry(self):
        """Vérifie si la péremption est critique (<= 7 jours)"""
        if not self.expiry_date or self.is_expired:
            return False
        return self.days_until_expiry <= 7
    
    @hybrid_property
    def has_low_stock(self):
        """Vérifie si le stock est bas"""
        return self.quantity <= self.alert_threshold and self.quantity > 0
    
    @hybrid_property
    def is_out_of_stock(self):
        """Vérifie si le produit est en rupture"""
        return self.quantity <= 0
    
    @hybrid_property
    def is_over_stock(self):
        """Vérifie si le stock est trop élevé"""
        if self.maximum_stock:
            return self.quantity > self.maximum_stock
        return False
    
    # =====================================
    # MÉTHODES
    # =====================================
    def update_stock_status(self):
        """Met à jour le statut du stock"""
        if self.is_out_of_stock:
            self.stock_status = "out_of_stock"
        elif self.has_low_stock:
            self.stock_status = "low_stock"
        elif self.is_over_stock:
            self.stock_status = "over_stock"
        else:
            self.stock_status = "normal"
        
        # Mettre à jour le statut de disponibilité
        self.is_available = not self.is_out_of_stock and self.is_active
    
    def update_expiry_status(self):
        """Met à jour le statut de péremption"""
        if not self.expiry_date:
            self.expiry_status = "unknown"
        elif self.is_expired:
            self.expiry_status = "expired"
        elif self.is_critical_expiry:
            self.expiry_status = "critical"
        elif self.is_expiring_soon:
            self.expiry_status = "warning"
        else:
            self.expiry_status = "ok"
    
    def adjust_quantity(self, amount: int, reason: str, user_id: UUID = None):
        """Ajuste la quantité du produit"""
        new_quantity = self.quantity + amount
        
        if new_quantity < 0:
            raise ValueError("La quantité ne peut pas être négative")
        
        self.quantity = new_quantity
        self.available_quantity = max(0, new_quantity - self.reserved_quantity)
        self.last_adjustment_date = datetime.utcnow()
        
        # Mettre à jour les statuts
        self.update_stock_status()
        self.update_expiry_status()
        
        # Créer un mouvement de stock (à implémenter avec le modèle StockMovement)
        return self
    
    def reserve_quantity(self, amount: int):
        """Réserve une quantité pour une vente en attente"""
        if amount > self.available_quantity:
            raise ValueError("Quantité non disponible")
        
        self.reserved_quantity += amount
        self.available_quantity -= amount
        return self
    
    def release_reservation(self, amount: int):
        """Libère une réservation"""
        if amount > self.reserved_quantity:
            raise ValueError("Quantité réservée insuffisante")
        
        self.reserved_quantity -= amount
        self.available_quantity += amount
        return self
    
    def calculate_prices(self, margin_percent: float = None, tva_rate: float = None):
        """Calcule automatiquement les prix"""
        if margin_percent is None:
            # Utiliser la marge par défaut du tenant
            margin_percent = 30.0
        
        if tva_rate is None:
            tva_rate = self.tva_rate if self.has_tva else 0.0
        
        # Calculer le prix de vente HT
        selling_price_ht = self.purchase_price * (1 + margin_percent / 100)
        
        # Ajouter la TVA si nécessaire
        if self.has_tva:
            self.selling_price = selling_price_ht * (1 + tva_rate / 100)
        else:
            self.selling_price = selling_price_ht
        
        # Mettre à jour les marges (se mettent à jour automatiquement via les computed columns)
        return self
    
    def to_dict(self, include_details: bool = False) -> Dict[str, Any]:
        """Convertit le produit en dictionnaire"""
        data = {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "code": self.code,
            "barcode": self.barcode,
            "name": self.name,
            "commercial_name": self.commercial_name,
            "category": self.category,
            "product_type": self.product_type,
            
            # Stock
            "quantity": self.quantity,
            "available_quantity": self.available_quantity,
            "reserved_quantity": self.reserved_quantity,
            "unit": self.unit,
            "alert_threshold": self.alert_threshold,
            "minimum_stock": self.minimum_stock,
            "maximum_stock": self.maximum_stock,
            
            # Prix
            "purchase_price": float(self.purchase_price),
            "selling_price": float(self.selling_price),
            "wholesale_price": float(self.wholesale_price) if self.wholesale_price else None,
            "tva_rate": float(self.tva_rate),
            "has_tva": self.has_tva,
            "margin_amount": float(self.margin_amount) if self.margin_amount else 0,
            "margin_rate": float(self.margin_rate) if self.margin_rate else 0,
            
            # Péremption
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "batch_number": self.batch_number,
            "days_until_expiry": self.days_until_expiry,
            
            # Statuts
            "stock_status": self.stock_status,
            "expiry_status": self.expiry_status,
            "is_active": self.is_active,
            "is_available": self.is_available,
            "is_expired": self.is_expired,
            "is_expiring_soon": self.is_expiring_soon,
            "is_critical_expiry": self.is_critical_expiry,
            "has_low_stock": self.has_low_stock,
            "is_out_of_stock": self.is_out_of_stock,
            "is_over_stock": self.is_over_stock,
            
            # Valeurs calculées
            "purchase_value": float(self.purchase_value),
            "selling_value": float(self.selling_value),
            "total_margin": float(self.total_margin),
            
            # Timestamps
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_details:
            data.update({
                "description": self.description,
                "active_ingredient": self.active_ingredient,
                "dosage": self.dosage,
                "galenic_form": self.galenic_form,
                "laboratory": self.laboratory,
                "dci": self.dci,
                "subcategory": self.subcategory,
                "therapeutic_class": self.therapeutic_class,
                "location": self.location,
                "packaging": self.packaging,
                "prescription_required": self.prescription_required,
                "regulatory_class": self.regulatory_class,
                "main_supplier": self.main_supplier,
                "supplier_code": self.supplier_code,
                "supplier_price": float(self.supplier_price) if self.supplier_price else None,
                "image_url": self.image_url,
                "leaflet_url": self.leaflet_url,
                "notes": self.notes,
                "authorization_number": self.authorization_number,
                
                # Statistiques
                "total_sold": self.total_sold,
                "total_purchased": self.total_purchased,
                "last_sale_date": self.last_sale_date.isoformat() if self.last_sale_date else None,
                "last_purchase_date": self.last_purchase_date.isoformat() if self.last_purchase_date else None,
            })
        
        return data
    
    def __repr__(self) -> str:
        return f"<Product {self.code or 'NoCode'}: {self.name} (Stock: {self.quantity})>"

class ProductStock(Base):
    """
    Modèle représentant le stock par lot pour les produits.
    Gère la traçabilité par lot et date de péremption.
    """
    __tablename__ = "product_stocks"
    
    # =====================================
    # IDENTIFIANT UNIQUE
    # =====================================
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey('tenants.id'), nullable=False, index=True)
    product_id = Column(UUID(as_uuid=True), ForeignKey('products.id'), nullable=False, index=True)
    
    # =====================================
    # INFORMATION DU LOT
    # =====================================
    batch_number = Column(String(100), nullable=False, index=True)
    expiry_date = Column(Date, nullable=False, index=True)
    
    # =====================================
    # QUANTITÉS
    # =====================================
    quantity_received = Column(Integer, default=0, nullable=False)
    quantity_available = Column(Integer, default=0, nullable=False)
    quantity_reserved = Column(Integer, default=0, nullable=False)
    quantity_sold = Column(Integer, default=0, nullable=False)
    quantity_lost = Column(Integer, default=0, nullable=False)
    quantity_damaged = Column(Integer, default=0, nullable=False)
    
    # =====================================
    # PRIX COUTANT
    # =====================================
    cost_price = Column(Numeric(12, 2), nullable=False, default=0.0)
    
    # =====================================
    # FOURNISSEUR
    # =====================================
    supplier_id = Column(UUID(as_uuid=True), ForeignKey('suppliers.id'), nullable=True)
    supplier_name = Column(String(200), nullable=True)
    invoice_number = Column(String(100), nullable=True)
    purchase_date = Column(Date, nullable=True)
    
    # =====================================
    # EMPLACEMENT
    # =====================================
    location = Column(String(100), nullable=True)
    shelf = Column(String(50), nullable=True)
    
    # =====================================
    # STATUT
    # =====================================
    is_active = Column(Boolean, default=True, index=True)
    status = Column(String(20), default="available", 
                   comment="available, reserved, sold, expired, damaged, lost")
    
    # =====================================
    # TIMESTAMPS
    # =====================================
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # =====================================
    # RELATIONS
    # =====================================
    product = relationship("Product", back_populates="product_stocks")
    tenant = relationship("Tenant")
    #supplier = relationship("Supplier", back_populates="stock_items")
    
    # =====================================
    # INDEXES
    # =====================================
    __table_args__ = (
        Index('ix_product_stocks_batch_expiry', 'batch_number', 'expiry_date'),
        Index('ix_product_stocks_product_status', 'product_id', 'status'),
        Index('ix_product_stocks_expiry_status', 'expiry_date', 'status'),
        Index('ix_product_stocks_tenant_active', 'tenant_id', 'is_active'),
    )
    
    # =====================================
    # VALIDATIONS
    # =====================================
    @validates('expiry_date')
    def validate_expiry_date(self, key, value):
        """Valide la date de péremption"""
        if value and value < date.today():
            raise ValueError("La date de péremption ne peut pas être dans le passé")
        return value
    
    @validates('quantity_available', 'quantity_reserved')
    def validate_quantities(self, key, value):
        """Valide que les quantités ne sont pas négatives"""
        if value < 0:
            raise ValueError(f"La {key} ne peut pas être négative")
        return value
    
    # =====================================
    # PROPRIÉTÉS CALCULÉES
    # =====================================
    @hybrid_property
    def total_quantity(self):
        """Quantité totale du lot"""
        return (self.quantity_available + self.quantity_reserved + 
                self.quantity_sold + self.quantity_lost + self.quantity_damaged)
    
    @hybrid_property
    def is_expired(self):
        """Vérifie si le lot est périmé"""
        return self.expiry_date < date.today()
    
    @hybrid_property
    def days_until_expiry(self):
        """Jours restants avant péremption"""
        today = date.today()
        return (self.expiry_date - today).days
    
    @hybrid_property
    def is_expiring_soon(self):
        """Vérifie si le lot expire bientôt (<= 30 jours)"""
        return 0 < self.days_until_expiry <= 30
    
    @hybrid_property
    def is_critical_expiry(self):
        """Vérifie si la péremption est critique (<= 7 jours)"""
        return 0 < self.days_until_expiry <= 7
    
    @hybrid_property
    def stock_value(self):
        """Valeur du stock du lot"""
        return float(self.quantity_available * self.cost_price)
    
    # =====================================
    # MÉTHODES
    # =====================================
    def update_status(self):
        """Met à jour le statut du lot"""
        if self.is_expired:
            self.status = "expired"
        elif self.quantity_available == 0:
            self.status = "sold" if self.quantity_sold > 0 else "unavailable"
        elif self.quantity_reserved > 0:
            self.status = "reserved"
        else:
            self.status = "available"
    
    def reserve(self, quantity: int):
        """Réserve une quantité du lot"""
        if quantity > self.quantity_available:
            raise ValueError(f"Quantité disponible insuffisante. Disponible: {self.quantity_available}, Demandé: {quantity}")
        
        self.quantity_reserved += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self
    
    def release_reservation(self, quantity: int):
        """Libère une réservation"""
        if quantity > self.quantity_reserved:
            raise ValueError(f"Quantité réservée insuffisante. Réservée: {self.quantity_reserved}, Demandé: {quantity}")
        
        self.quantity_reserved -= quantity
        self.quantity_available += quantity
        self.update_status()
        return self
    
    def sell(self, quantity: int):
        """Enregistre une vente sur ce lot"""
        if quantity > self.quantity_available:
            raise ValueError(f"Quantité disponible insuffisante. Disponible: {self.quantity_available}, Demandé: {quantity}")
        
        self.quantity_sold += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self
    
    def adjust_quantity(self, new_quantity: int, reason: str):
        """Ajuste la quantité disponible"""
        if new_quantity < 0:
            raise ValueError("La quantité ne peut pas être négative")
        
        self.quantity_available = new_quantity
        self.update_status()
        return self
    
    def mark_damaged(self, quantity: int):
        """Marque une quantité comme endommagée"""
        if quantity > self.quantity_available:
            raise ValueError("Quantité disponible insuffisante")
        
        self.quantity_damaged += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self
    
    def mark_lost(self, quantity: int):
        """Marque une quantité comme perdue"""
        if quantity > self.quantity_available:
            raise ValueError("Quantité disponible insuffisante")
        
        self.quantity_lost += quantity
        self.quantity_available -= quantity
        self.update_status()
        return self
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit le stock de lot en dictionnaire"""
        return {
            "id": str(self.id),
            "product_id": str(self.product_id),
            "batch_number": self.batch_number,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "quantity_received": self.quantity_received,
            "quantity_available": self.quantity_available,
            "quantity_reserved": self.quantity_reserved,
            "quantity_sold": self.quantity_sold,
            "quantity_lost": self.quantity_lost,
            "quantity_damaged": self.quantity_damaged,
            "cost_price": float(self.cost_price),
            "supplier_name": self.supplier_name,
            "invoice_number": self.invoice_number,
            "purchase_date": self.purchase_date.isoformat() if self.purchase_date else None,
            "location": self.location,
            "shelf": self.shelf,
            "is_active": self.is_active,
            "status": self.status,
            "is_expired": self.is_expired,
            "days_until_expiry": self.days_until_expiry,
            "is_expiring_soon": self.is_expiring_soon,
            "is_critical_expiry": self.is_critical_expiry,
            "stock_value": float(self.stock_value),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
    
    def __repr__(self) -> str:
        return f"<ProductStock {self.batch_number} - {self.expiry_date} (Disponible: {self.quantity_available})>"
    
    stock_movements = relationship(
        "StockMovement",
        cascade="all, delete-orphan",
        lazy="selectin"
    )