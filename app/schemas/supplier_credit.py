# app/schemas/supplier_credit.py
"""
Schémas Pydantic pour la gestion du crédit fournisseurs
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, Field, validator


class SupplierCreditConfigCreate(BaseModel):
    supplier_id: UUID
    name: str
    description: Optional[str] = None
    is_default: bool = False
    max_credit_amount: Optional[Decimal] = None
    max_credit_days: Optional[int] = None
    interest_rate: Decimal = Field(default=0, ge=0, le=100)
    late_fee_rate: Decimal = Field(default=0, ge=0, le=100)
    payment_frequency: str = "per_sale"
    repayment_percentage_of_sale: Decimal = Field(default=30, ge=0, le=100)
    min_repayment_amount: Decimal = Field(default=0, ge=0)
    max_repayment_amount: Optional[Decimal] = Field(None, ge=0)
    custom_due_dates: List[date] = []
    grace_period_days: int = Field(default=0, ge=0)
    repayment_priority: int = Field(default=1, ge=1)
    auto_repayment_enabled: bool = True
    send_reminders: bool = True
    reminder_days_before: int = Field(default=3, ge=0)
    notes: Optional[str] = None
    meta_data: Dict[str, Any] = Field(default_factory=dict)

    @validator("payment_frequency")
    def validate_frequency(cls, v):
        allowed = ["per_sale", "per_day", "per_week", "per_month", "fixed_date", "custom"]
        if v not in allowed:
            raise ValueError(f"Frequence doit être parmi {allowed}")
        return v


class SupplierCreditConfigResponse(BaseModel):
    id: UUID
    supplier_id: UUID
    name: str
    description: Optional[str]
    is_default: bool
    is_active: bool
    max_credit_amount: Optional[Decimal]
    max_credit_days: Optional[int]
    interest_rate: Decimal
    late_fee_rate: Decimal
    payment_frequency: str
    repayment_percentage_of_sale: Decimal
    min_repayment_amount: Decimal
    max_repayment_amount: Optional[Decimal]
    auto_repayment_enabled: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ManualRepaymentRequest(BaseModel):
    supplier_id: UUID
    amount: Decimal = Field(..., gt=0)
    payment_reference: str
    notes: Optional[str] = None


class SupplierBalanceResponse(BaseModel):
    supplier_id: str
    supplier_name: Optional[str] = None
    total_credit: float
    total_repaid: float
    current_debt: float
    accrued_interest: float
    late_fees: float
    debt_ratio: float
    status: str
    first_credit_date: Optional[str]
    last_repayment_date: Optional[str]
    next_due_date: Optional[str]
    active_credits: List[Dict[str, Any]]


class AdjustedCapitalResponse(BaseModel):
    id: UUID
    pharmacy_id: str
    cash_in_hand: float
    bank_balance: float
    total_liquidities: float
    total_supplier_debt: float
    stock_value: float
    equipment_value: float
    gross_capital: float
    adjusted_capital: float
    equity_capital: float
    calculation_date: date


class RealProfitRequest(BaseModel):
    start_date: date
    end_date: date


class RealProfitResponse(BaseModel):
    period: Dict[str, str]
    total_sales: float
    total_cogs: float
    gross_profit: float
    gross_margin: float
    total_expenses: float
    total_repayments: float
    net_profit: float
    net_margin: float
    real_capital_generated: float
    explanation: Dict[str, str]


class SaleRepaymentResponse(BaseModel):
    id: UUID
    sale_id: str
    supplier_id: str
    allocated_repayment: float
    capital_portion: float
    quantity_sold: int
    sale_date: date

    class Config:
        from_attributes = True


class PurchaseCreditResponse(BaseModel):
    id: UUID
    purchase_id: str
    supplier_id: str
    credit_amount: float
    repaid_amount: float
    remaining_amount: float
    repayment_percentage: float
    due_date: date
    status: str
    repayment_progress: float

    class Config:
        from_attributes = True