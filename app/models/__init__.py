# app/models/__init__.py
"""
Initialisation des modèles.
Objectif: importer les modèles pour enregistrer les tables dans Base.metadata
(et pour qu'Alembic les détecte).
"""

# =========================
# Modèles SaaS / Core
# =========================
from app.models.user_subscription import UserSubscription 
from .tenant import Tenant
from .user import User
from .subscription import Subscription
from .pharmacy import Pharmacy
from .user_pharmacy import UserPharmacy
from .sync_log import SyncLog

# =========================
# Gestion / Business
# =========================
from .client import Client
from .product import Product, ProductStock
from .sale import Sale

from .invoice import Invoice
from .invoice_payment import InvoicePayment

from .purchase import Purchase, PurchaseItem, PurchasePayment

from .inventory import PhysicalInventory, InventoryItem, InventorySchedule

from .finance import FinancialPeriod, FinancialTransaction, Expense
from .cost import Cost, Budget, Supplier

from .audit_log import AuditLog
from .refund import Refund
from .debt import Debt
from .debt_payment import DebtPayment
from .payment import Payment
from app.models.pharmacy import Pharmacy, PharmacyConfig
from .transfert import ProductTransfer, TransferItem, TransferStatus, TransferType, TransferPriority
from .stock_movement import StockMovement, InventoryCount, InventoryCountItem
from app.models.stock_adjustment import StockAdjustment, StockAdjustmentItem
from app.models.customer import Customer  
from app.models.branch import Branch 
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.category import Category
from app.models.user_session import UserSession
from app.models.order import Order, OrderStatus, PaymentStatus
from app.models.capital import Capital, CapitalAccount, CapitalTransaction, Turnover
from app.models.user_history import UserHistory
from app.models.trash_bin import TrashBin

__all__ = [
    # Core
    "Tenant",
    "User",
    "Subscription",
    "Pharmacy",
    "UserPharmacy",
    "SyncLog",
    # Business
    "Client",
    "Product",
    "Sale",
    "Invoice",
    "InvoicePayment",
    "InventoryCount",
    "InventoryCountItem",
    "Purchase",
    "PurchaseItem",
    "PurchasePayment",
    "PhysicalInventory",
    "InventoryItem",
    "InventorySchedule",
    "FinancialPeriod",
    "FinancialTransaction",
    "Capital",
    "Expense",
    "Cost",
    "Budget",
    "Supplier",
    "AuditLog",
    "Refund",
    "Debt",
    "DebtPayment",
    "Payment",
    "ProductTransfer",
    "TransferItem",
    "TransferStatus",
    "TransferType",
    "Customer",
    "Branch",
    "UserSubscription",
    "SubscriptionCode",
    "SubscriptionCodeStatus",
    "Category",
    "StockAdjustment",
    "StockAdjustmentItem",
    "UserSession",
    "TransferPriority",
    "StockMovement",
    "ProductStock",
    "Order",
    "OrderStatus",
    "PaymentStatus",
    "CapitalAccount", 
    "CapitalTransaction",
    "Turnover",
    "UserHistory",
    "TrashBin",
    "PharmacyConfig"
]