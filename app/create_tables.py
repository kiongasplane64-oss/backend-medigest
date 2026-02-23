# app/create_tables.py
"""
Script simple pour créer les tables
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_all_tables():
    """Crée toutes les tables de la base de données"""
    try:
        from app.db.session import Base, engine
        
        # Importez TOUS les modèles pour qu'ils soient enregistrés
        # Utilisez le fichier __init__.py des modèles
        from app.models import (
            Tenant, User, Client, Cost, Budget, Supplier,
            Invoice, InvoiceItem, InvoicePayment,
            PhysicalInventory, InventoryItem, InventorySchedule,
            Debt, DebtPayment,
            FinancialPeriod, FinancialTransaction, Capital, Expense,
            AuditLog
        )
        
        logger.info("Création des tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Toutes les tables ont été créées avec succès!")
        
    except Exception as e:
        logger.error(f"❌ Erreur lors de la création des tables: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    create_all_tables()