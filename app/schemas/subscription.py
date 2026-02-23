# app/schemas/subscription.py
from pydantic import BaseModel, Field, validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from uuid import UUID
from decimal import Decimal
from enum import Enum

from app.models.subscription import (
    SubscriptionPlan, 
    BillingPeriod, 
    SubscriptionStatus,
    PaymentStatus,
    PaymentMethod
)


# =======================
# ENUMS POUR VALIDATION
# =======================
class SubscriptionPlanEnum(str, Enum):
    starter = "starter"
    professional = "professional"
    enterprise = "enterprise"
    essai = "essai"


class BillingPeriodEnum(str, Enum):
    monthly = "monthly"
    quarterly = "quarterly"
    annual = "annual"


class SubscriptionStatusEnum(str, Enum):
    active = "active"
    pending = "pending"
    trial = "trial"
    expired = "expired"
    suspended = "suspended"
    cancelled = "cancelled"


class PaymentStatusEnum(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"
    partially_refunded = "partially_refunded"


class PaymentMethodEnum(str, Enum):
    cash = "cash"
    mobile_money = "mobile_money"
    bank_transfer = "bank_transfer"
    card = "card"
    other = "other"


# =======================
# SCHÉMAS DE BASE
# =======================
class SubscriptionBase(BaseModel):
    """Schéma de base pour un abonnement"""
    plan: SubscriptionPlanEnum = Field(default=SubscriptionPlanEnum.starter, description="Plan d'abonnement")
    plan_name: Optional[str] = Field(None, max_length=100, description="Nom du plan affiché")
    billing_period: BillingPeriodEnum = Field(default=BillingPeriodEnum.monthly, description="Période de facturation")
    status: SubscriptionStatusEnum = Field(default=SubscriptionStatusEnum.trial, description="Statut de l'abonnement")
    
    # Prix
    monthly_price: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Prix mensuel")
    annual_price: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Prix annuel")
    current_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'), description="Prix actuel selon période")
    
    # Taxes et remises
    tax_rate: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Taux de TVA (%)")
    discount_percent: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Remise en pourcentage (%)")
    discount_amount: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Montant de remise fixe")
    
    # Dates
    start_date: Optional[datetime] = Field(None, description="Date de début")
    end_date: Optional[datetime] = Field(None, description="Date de fin")
    trial_end_date: Optional[datetime] = Field(None, description="Fin de la période d'essai")
    next_billing_date: Optional[datetime] = Field(None, description="Prochaine date de facturation")
    cancellation_date: Optional[datetime] = Field(None, description="Date d'annulation")
    
    # Limites
    max_users: int = Field(default=3, ge=1, description="Nombre maximum d'utilisateurs")
    max_products: Optional[int] = Field(None, ge=0, description="Nombre maximum de produits")
    max_storage_mb: int = Field(default=1024, ge=10, description="Stockage maximum en MB")
    
    # Fonctionnalités
    features: Optional[List[str]] = Field(None, description="Liste des fonctionnalités incluses")
    
    # Configuration
    auto_renew: bool = Field(default=True, description="Renouvellement automatique")
    notes: Optional[str] = Field(None, description="Notes internes")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Métadonnées additionnelles")
    
    model_config = ConfigDict(from_attributes=True)

    @validator('plan_name', pre=True, always=True)
    def set_plan_name(cls, v, values):
        """Définit automatiquement le nom du plan si non fourni"""
        if v is None and 'plan' in values:
            plan = values['plan']
            plan_names = {
                SubscriptionPlanEnum.starter: "Starter",
                SubscriptionPlanEnum.professional: "Professional",
                SubscriptionPlanEnum.enterprise: "Enterprise",
                SubscriptionPlanEnum.essai: "Essai Gratuit"
            }
            return plan_names.get(plan, str(plan).title())
        return v
    
    @validator('current_price', pre=True, always=True)
    def set_current_price(cls, v, values):
        """Définit automatiquement le prix actuel selon la période"""
        if v is None:
            billing_period = values.get('billing_period')
            monthly_price = values.get('monthly_price')
            annual_price = values.get('annual_price')
            
            if billing_period == BillingPeriodEnum.monthly and monthly_price:
                return monthly_price
            elif billing_period == BillingPeriodEnum.annual and annual_price:
                return annual_price
            elif billing_period == BillingPeriodEnum.quarterly and monthly_price:
                # Trimestriel = 3 x mensuel
                return monthly_price * 3
        return v
    
    @validator('end_date')
    def validate_end_date(cls, v, values):
        """Validation de la date de fin"""
        start_date = values.get('start_date')
        if start_date and v and v <= start_date:
            raise ValueError('La date de fin doit être après la date de début')
        return v
    
    @validator('trial_end_date')
    def validate_trial_end_date(cls, v, values):
        """Validation de la date de fin d'essai"""
        start_date = values.get('start_date')
        if start_date and v and v <= start_date:
            raise ValueError('La date de fin d\'essai doit être après la date de début')
        
        # Vérifier que la période d'essai ne dépasse pas 90 jours
        if start_date and v:
            trial_days = (v - start_date).days
            if trial_days > 90:
                raise ValueError('La période d\'essai ne peut pas dépasser 90 jours')
        return v
    
    @validator('max_users')
    def validate_max_users_by_plan(cls, v, values):
        """Validation du nombre d'utilisateurs selon le plan"""
        plan = values.get('plan')
        
        if plan == SubscriptionPlanEnum.starter and v > 10:
            raise ValueError('Le plan Starter est limité à 10 utilisateurs maximum')
        elif plan == SubscriptionPlanEnum.professional and v > 50:
            raise ValueError('Le plan Professional est limité à 50 utilisateurs maximum')
        # Enterprise n'a pas de limite
        
        return v
    
    @validator('features')
    def validate_features(cls, v):
        """Validation de la liste des fonctionnalités"""
        if v is not None:
            if not isinstance(v, list):
                raise ValueError('Les fonctionnalités doivent être une liste')
            for feature in v:
                if not isinstance(feature, str):
                    raise ValueError('Chaque fonctionnalité doit être une chaîne de caractères')
        return v


# =======================
# SCHÉMAS DE CRÉATION
# =======================
class SubscriptionCreate(SubscriptionBase):
    """Schéma pour la création d'un nouvel abonnement"""
    tenant_id: UUID = Field(..., description="ID du tenant (pharmacie)")
    subscription_code: Optional[str] = Field(None, max_length=50, description="Code d'abonnement unique")
    
    @validator('subscription_code', pre=True, always=True)
    def generate_subscription_code(cls, v):
        """Génère un code d'abonnement si non fourni"""
        if v is None:
            import uuid
            from datetime import datetime
            date_str = datetime.now().strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:8].upper()
            return f"SUB-{date_str}-{unique_part}"
        return v
    
    @validator('start_date', pre=True, always=True)
    def set_start_date(cls, v):
        """Définit la date de début par défaut"""
        if v is None:
            return datetime.now()
        return v


class SubscriptionTrialCreate(BaseModel):
    """Schéma pour créer un abonnement d'essai"""
    plan: SubscriptionPlanEnum = Field(default=SubscriptionPlanEnum.essai, description="Plan d'essai")
    trial_days: int = Field(default=14, ge=1, le=90, description="Durée de l'essai en jours")
    tenant_id: UUID = Field(..., description="ID du tenant")


class SubscriptionUpgradeRequest(BaseModel):
    """Demande de mise à niveau d'abonnement"""
    new_plan: SubscriptionPlanEnum = Field(..., description="Nouveau plan")
    new_billing_period: Optional[BillingPeriodEnum] = Field(None, description="Nouvelle période de facturation")
    immediate: bool = Field(default=False, description="Appliquer immédiatement")
    pro_rated: bool = Field(default=True, description="Ajustement pro-rata")
    reason: Optional[str] = Field(None, max_length=500, description="Raison de la mise à niveau")


class SubscriptionRenewalRequest(BaseModel):
    """Demande de renouvellement d'abonnement"""
    billing_period: Optional[BillingPeriodEnum] = Field(None, description="Période de facturation pour le renouvellement")
    auto_renew: Optional[bool] = Field(None, description="Activer/désactiver le renouvellement automatique")
    payment_method: Optional[PaymentMethodEnum] = Field(None, description="Méthode de paiement")


# =======================
# SCHÉMAS DE MISE À JOUR
# =======================
class SubscriptionUpdate(BaseModel):
    """Schéma pour mettre à jour un abonnement existant"""
    plan: Optional[SubscriptionPlanEnum] = None
    plan_name: Optional[str] = Field(None, max_length=100)
    billing_period: Optional[BillingPeriodEnum] = None
    status: Optional[SubscriptionStatusEnum] = None
    
    # Prix
    monthly_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    annual_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    current_price: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    
    # Taxes et remises
    tax_rate: Optional[Decimal] = Field(None, ge=Decimal('0.00'), le=Decimal('100.00'))
    discount_percent: Optional[Decimal] = Field(None, ge=Decimal('0.00'), le=Decimal('100.00'))
    discount_amount: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    
    # Dates
    end_date: Optional[datetime] = None
    trial_end_date: Optional[datetime] = None
    next_billing_date: Optional[datetime] = None
    cancellation_date: Optional[datetime] = None
    
    # Limites
    max_users: Optional[int] = Field(None, ge=1)
    max_products: Optional[int] = Field(None, ge=0)
    max_storage_mb: Optional[int] = Field(None, ge=10)
    
    # Configuration
    auto_renew: Optional[bool] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionStatusUpdate(BaseModel):
    """Mise à jour manuelle du statut"""
    status: SubscriptionStatusEnum = Field(..., description="Nouveau statut")
    reason: str = Field(..., min_length=5, max_length=1000, description="Raison du changement")
    effective_date: Optional[datetime] = Field(None, description="Date d'effet")
    notes: Optional[str] = Field(None, description="Notes additionnelles")
    
    @validator('effective_date')
    def validate_effective_date(cls, v):
        if v and v < datetime.now():
            raise ValueError('La date d\'effet ne peut pas être dans le passé')
        return v


# =======================
# SCHÉMAS DE RÉPONSE
# =======================
class SubscriptionResponse(SubscriptionBase):
    """Schéma de réponse complet pour un abonnement"""
    id: UUID
    tenant_id: UUID
    subscription_code: str
    
    # Calculs
    total_amount: Decimal = Field(..., ge=Decimal('0.00'), description="Montant total après taxes et remises")
    days_remaining: int = Field(..., ge=0, description="Jours restants avant expiration")
    trial_days_remaining: Optional[int] = Field(None, ge=0, description="Jours d'essai restants")
    is_active: bool = Field(..., description="L'abonnement est-il actif ?")
    is_trial: bool = Field(..., description="Est en période d'essai ?")
    
    # Audit
    created_at: datetime
    updated_at: datetime
    created_by: Optional[UUID] = None
    
    # Relations
    payments_count: Optional[int] = Field(0, description="Nombre de paiements")
    active_payments_count: Optional[int] = Field(0, description="Nombre de paiements actifs")
    
    model_config = ConfigDict(from_attributes=True)
    
    @validator('total_amount', pre=True)
    def calculate_total_amount(cls, v, values):
        """Calcule le montant total automatiquement"""
        if v is None:
            current_price = values.get('current_price', Decimal('0.00'))
            discount_percent = values.get('discount_percent', Decimal('0.00'))
            discount_amount = values.get('discount_amount', Decimal('0.00'))
            tax_rate = values.get('tax_rate', Decimal('0.00'))
            
            # Appliquer la remise en pourcentage
            if discount_percent > 0:
                discount = (current_price * discount_percent) / Decimal('100')
                current_price -= discount
            
            # Appliquer la remise fixe
            current_price -= discount_amount
            
            # S'assurer que le montant n'est pas négatif
            if current_price < 0:
                current_price = Decimal('0.00')
            
            # Ajouter les taxes
            if tax_rate > 0:
                tax_amount = (current_price * tax_rate) / Decimal('100')
                current_price += tax_amount
            
            return current_price.quantize(Decimal('0.01'))
        return v
    
    @validator('days_remaining', pre=True)
    def calculate_days_remaining(cls, v, values):
        """Calcule les jours restants"""
        if v is None:
            end_date = values.get('end_date')
            if end_date:
                now = datetime.now()
                if end_date > now:
                    return (end_date - now).days
            return 0
        return v
    
    @validator('trial_days_remaining', pre=True)
    def calculate_trial_days_remaining(cls, v, values):
        """Calcule les jours d'essai restants"""
        if v is None:
            trial_end_date = values.get('trial_end_date')
            status = values.get('status')
            if trial_end_date and status == SubscriptionStatusEnum.trial:
                now = datetime.now()
                if trial_end_date > now:
                    return (trial_end_date - now).days
            return 0
        return v
    
    @validator('is_active', pre=True)
    def calculate_is_active(cls, v, values):
        """Détermine si l'abonnement est actif"""
        if v is None:
            status = values.get('status')
            end_date = values.get('end_date')
            if end_date:
                return status == SubscriptionStatusEnum.active and end_date > datetime.now()
            return status == SubscriptionStatusEnum.active
        return v
    
    @validator('is_trial', pre=True)
    def calculate_is_trial(cls, v, values):
        """Détermine si l'abonnement est en essai"""
        if v is None:
            status = values.get('status')
            trial_end_date = values.get('trial_end_date')
            if trial_end_date:
                return status == SubscriptionStatusEnum.trial and trial_end_date > datetime.now()
            return status == SubscriptionStatusEnum.trial
        return v


class SubscriptionSummaryResponse(BaseModel):
    """Version allégée pour les listes"""
    id: UUID
    subscription_code: str
    tenant_id: UUID
    plan: str
    plan_name: str
    billing_period: str
    status: str
    current_price: Decimal
    start_date: datetime
    end_date: datetime
    days_remaining: int
    is_active: bool
    is_trial: bool
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionCreationResponse(BaseModel):
    """Réponse après création d'un abonnement"""
    message: str
    subscription: SubscriptionResponse
    next_steps: List[str] = Field(
        default_factory=lambda: [
            "1. Abonnement créé avec succès",
            "2. Période d'essai activée",
            "3. Limites configurées",
            "4. Prêt à utiliser"
        ]
    )


# =======================
# SCHÉMAS DE PAIEMENT
# =======================
class PaymentBase(BaseModel):

    """Schéma de base pour un paiement"""
    subscription_id: UUID = Field(..., description="ID de l'abonnement")
    amount: Decimal = Field(..., gt=Decimal('0.00'), description="Montant dû")
    amount_paid: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), description="Montant payé")
    status: PaymentStatusEnum = Field(default=PaymentStatusEnum.pending, description="Statut du paiement")
    payment_method: PaymentMethodEnum = Field(..., description="Méthode de paiement")
    payment_reference: Optional[str] = Field(None, max_length=100, description="Référence du paiement")
    
    # Période couverte
    period_start: datetime = Field(..., description="Début de la période couverte")
    period_end: datetime = Field(..., description="Fin de la période couverte")
    
    # Métadonnées
    description: Optional[str] = Field(None, description="Description")
    notes: Optional[str] = Field(None, description="Notes")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Métadonnées additionnelles")
    
    model_config = ConfigDict(from_attributes=True)

    @validator('period_end')
    def validate_period_end(cls, v, values):
        """Validation de la période"""
        period_start = values.get('period_start')
        if period_start and v <= period_start:
            raise ValueError('period_end doit être après period_start')
        return v
    
    @validator('amount_paid')
    def validate_amount_paid(cls, v, values):
        """Validation du montant payé"""
        amount = values.get('amount')
        if amount and v > amount:
            raise ValueError('Le montant payé ne peut pas dépasser le montant dû')
        return v


class PaymentCreate(PaymentBase):
    """Schéma pour créer un nouveau paiement"""
    payment_code: Optional[str] = Field(None, max_length=50, description="Code de paiement unique")
    
    @validator('payment_code', pre=True, always=True)
    def generate_payment_code(cls, v):
        """Génère un code de paiement si non fourni"""
        if v is None:
            import uuid
            from datetime import datetime
            date_str = datetime.now().strftime('%Y%m%d')
            unique_part = uuid.uuid4().hex[:8].upper()
            return f"PAY-{date_str}-{unique_part}"
        return v


class PaymentUpdate(BaseModel):
    """Schéma pour mettre à jour un paiement"""
    amount_paid: Optional[Decimal] = Field(None, ge=Decimal('0.00'))
    status: Optional[PaymentStatusEnum] = None
    payment_method: Optional[PaymentMethodEnum] = None
    payment_reference: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    notes: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    paid_at: Optional[datetime] = Field(None, description="Date de paiement effectif")
    
    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(PaymentBase):
    """Schéma de réponse complet pour un paiement"""
    id: UUID
    payment_code: str
    
    # Audit
    created_at: datetime
    updated_at: datetime
    paid_at: Optional[datetime] = None
    
    # Calculs
    is_complete: bool = Field(..., description="Le paiement est-il complet ?")
    amount_due: Decimal = Field(..., description="Montant restant dû")
    
    model_config = ConfigDict(from_attributes=True)
    
    @validator('is_complete', pre=True)
    def calculate_is_complete(cls, v, values):
        """Détermine si le paiement est complet"""
        if v is None:
            status = values.get('status')
            amount = values.get('amount')
            amount_paid = values.get('amount_paid')
            
            return (
                status == PaymentStatusEnum.completed and
                amount_paid >= amount
            )
        return v
    
    @validator('amount_due', pre=True)
    def calculate_amount_due(cls, v, values):
        """Calcule le montant restant dû"""
        if v is None:
            amount = values.get('amount', Decimal('0.00'))
            amount_paid = values.get('amount_paid', Decimal('0.00'))
            return amount - amount_paid
        return v


# =======================
# SCHÉMAS DE FACTURATION
# =======================
class InvoiceItem(BaseModel):
    """Élément de facture"""
    description: str = Field(..., max_length=200, description="Description")
    quantity: Decimal = Field(default=Decimal('1.00'), gt=Decimal('0.00'), description="Quantité")
    unit_price: Decimal = Field(..., gt=Decimal('0.00'), description="Prix unitaire")
    tax_rate: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Taux de taxe (%)")
    discount_percent: Decimal = Field(default=Decimal('0.00'), ge=Decimal('0.00'), le=Decimal('100.00'), description="Remise (%)")
    
    @property
    def subtotal(self) -> Decimal:
        """Sous-total avant taxes et remises"""
        return (self.quantity * self.unit_price).quantize(Decimal('0.01'))
    
    @property
    def discount_amount(self) -> Decimal:
        """Montant de la remise"""
        return (self.subtotal * self.discount_percent / Decimal('100')).quantize(Decimal('0.01'))
    
    @property
    def taxable_amount(self) -> Decimal:
        """Montant taxable"""
        return (self.subtotal - self.discount_amount).quantize(Decimal('0.01'))
    
    @property
    def tax_amount(self) -> Decimal:
        """Montant de la taxe"""
        return (self.taxable_amount * self.tax_rate / Decimal('100')).quantize(Decimal('0.01'))
    
    @property
    def total(self) -> Decimal:
        """Total de l'élément"""
        return (self.taxable_amount + self.tax_amount).quantize(Decimal('0.01'))


class InvoiceCreate(BaseModel):
    """Création d'une facture"""
    subscription_id: UUID = Field(..., description="ID de l'abonnement")
    invoice_date: date = Field(default_factory=lambda: date.today(), description="Date de facturation")
    due_date: date = Field(..., description="Date d'échéance")
    items: List[InvoiceItem] = Field(..., min_items=1, description="Éléments de facturation")
    notes: Optional[str] = Field(None, description="Notes")
    
    @validator('due_date')
    def validate_due_date(cls, v, values):
        """Validation de la date d'échéance"""
        invoice_date = values.get('invoice_date')
        if invoice_date and v <= invoice_date:
            raise ValueError('La date d\'échéance doit être après la date de facturation')
        return v
    
    @property
    def subtotal(self) -> Decimal:
        """Sous-total de la facture"""
        total = Decimal('0.00')
        for item in self.items:
            total += item.subtotal
        return total.quantize(Decimal('0.01'))
    
    @property
    def total_discount(self) -> Decimal:
        """Total des remises"""
        total = Decimal('0.00')
        for item in self.items:
            total += item.discount_amount
        return total.quantize(Decimal('0.01'))
    
    @property
    def total_tax(self) -> Decimal:
        """Total des taxes"""
        total = Decimal('0.00')
        for item in self.items:
            total += item.tax_amount
        return total.quantize(Decimal('0.01'))
    
    @property
    def grand_total(self) -> Decimal:
        """Total général"""
        total = Decimal('0.00')
        for item in self.items:
            total += item.total
        return total.quantize(Decimal('0.01'))


class InvoiceResponse(InvoiceCreate):
    """Réponse de facture"""
    id: UUID
    invoice_number: str = Field(..., description="Numéro de facture")
    status: str = Field(..., description="Statut de la facture")
    amount_paid: Decimal = Field(default=Decimal('0.00'), description="Montant payé")
    
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    # =======================
    # Champs calculés
    # =======================
    @property
    def subtotal(self) -> Decimal:
        """Sous-total calculé automatiquement"""
        return super().subtotal

    @property
    def total_discount(self) -> Decimal:
        """Total des remises calculé automatiquement"""
        return super().total_discount

    @property
    def total_tax(self) -> Decimal:
        """Total des taxes calculé automatiquement"""
        return super().total_tax

    @property
    def grand_total(self) -> Decimal:
        """Total général calculé automatiquement"""
        return super().grand_total

    @property
    def balance_due(self) -> Decimal:
        """Solde restant dû"""
        return (self.grand_total - self.amount_paid).quantize(Decimal('0.01'))


# =======================
# SCHÉMAS DE RAPPORT
# =======================
class SubscriptionAnalytics(BaseModel):
    """Analytics d'abonnement"""
    total_subscriptions: int = 0
    active_subscriptions: int = 0
    trial_subscriptions: int = 0
    expired_subscriptions: int = 0
    cancelled_subscriptions: int = 0
    
    # Par plan
    by_plan: Dict[str, int] = Field(default_factory=dict)
    
    # Chiffre d'affaires
    monthly_revenue: Decimal = Decimal('0.00')
    annual_revenue: Decimal = Decimal('0.00')
    total_revenue: Decimal = Decimal('0.00')
    
    # Taux de rétention
    renewal_rate: float = 0.0
    churn_rate: float = 0.0
    trial_conversion_rate: float = 0.0
    
    # Période
    period_start: date
    period_end: date
    
    model_config = ConfigDict(from_attributes=True)


class SubscriptionUsage(BaseModel):
    """Utilisation des ressources d'abonnement"""
    subscription_id: UUID
    tenant_id: UUID
    
    # Utilisateurs
    user_count: int = 0
    user_limit: int = 0
    user_usage_percent: float = 0.0
    
    # Produits
    product_count: int = 0
    product_limit: Optional[int] = None
    product_usage_percent: Optional[float] = None
    
    # Stockage
    storage_used_mb: int = 0
    storage_limit_mb: int = 0
    storage_usage_percent: float = 0.0
    
    # API
    api_calls_today: int = 0
    api_calls_limit: int = 0
    api_usage_percent: float = 0.0
    
    # Période
    period_start: date
    period_end: date
    
    model_config = ConfigDict(from_attributes=True)


# =======================
# SCHÉMAS D'EXPORT
# =======================
class SubscriptionExportRequest(BaseModel):
    """Demande d'export des données d'abonnement"""
    include_data: List[str] = Field(
        default_factory=lambda: ["subscriptions", "payments", "invoices"],
        description="Données à inclure"
    )
    format: str = Field(default="json", pattern="^(json|csv|excel)$", description="Format d'export")
    period_start: Optional[date] = Field(None, description="Début de la période")
    period_end: Optional[date] = Field(None, description="Fin de la période")
    compress: bool = Field(default=False, description="Compresser les données")
    
    @validator('include_data')
    def validate_include_data(cls, v):
        allowed_data = ["subscriptions", "payments", "invoices", "usage", "analytics"]
        for data_type in v:
            if data_type not in allowed_data:
                raise ValueError(f"Type de données non autorisé: {data_type}")
        return v
    
    @validator('period_end')
    def validate_period(cls, v, values):
        period_start = values.get('period_start')
        if period_start and v and v <= period_start:
            raise ValueError('period_end doit être après period_start')
        return 

class SubscriptionInDB(SubscriptionResponse):
    """Schéma pour les données stockées en base de données"""
    model_config = ConfigDict(from_attributes=True)