# app/models/__init__.py
"""
Fichier d'initialisation des modèles - Importez UNIQUEMENT les modèles qui existent
"""

# =====================================
# MODÈLES DE BASE 
# =====================================
from .tenant import Tenant
from .user import User
from .cost import Cost, Budget, Supplier
from .sync_log import SyncLog
from .subscription import Subscription
from .pharmacy import Pharmacy
from .user_pharmacy import UserPharmacy
# =====================================
# MODÈLES OPTIONNELS 
# =====================================

from .client import Client
from .product import Product
from .sale import Sale
from .debt import Debt
from .debt_payment import DebtPayment
from .invoice import Invoice, InvoiceItem
from .inventory import PhysicalInventory, InventoryItem, InventorySchedule  
from .finance import FinancialPeriod, FinancialTransaction, Capital, Expense  
from .audit_log import AuditLog 
from .refund import Refund, relationship
from .product import Product
from .purchase import Purchase, PurchaseItem, PurchasePayment
from .transfert import ProductTransfer, TransferItem, TransferStatus, TransferType
from .invoice_payment import InvoicePayment
from .payment import Payment


# =====================================
# LISTE DES MODÈLES DISPONIBLES
# =====================================
__all__ = [
    'Tenant',
    'User',
    'Cost',
    'Budget',
    'Supplier',
    'Client',
    'Product',
    'Sale',
    'Refund',
    'Debt',
    'DebtPayment',
    "Purchase",
    "PurchaseItem", 
    "PurchasePayment",
    "Pharmacy",
    "UserPharmacy",
    'ProductTransfer',
    'TransferItem',
    'Payment',
    'InvoicePayment',
    'InvoiceItem'
    
]