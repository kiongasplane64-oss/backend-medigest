"""add_role_column_to_pharmacy

Revision ID: bad8354d3ea9
Revises: 954814e9b3c3
Create Date: 2026-03-21 22:01:23.071565

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import json
from datetime import datetime


# revision identifiers, used by Alembic.
revision: str = 'bad8354d3ea9'
down_revision: Union[str, Sequence[str], None] = '954814e9b3c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Ajoute les nouveaux champs de configuration aux pharmacies
    Version avec Python (plus simple, évite les problèmes de type)
    """
    conn = op.get_bind()
    
    # Récupérer toutes les pharmacies
    result = conn.execute(
        sa.text("SELECT id, config FROM pharmacies")
    )
    
    pharmacies = result.fetchall()
    updated_count = 0
    
    # Valeurs par défaut pour les nouveaux champs
    default_sales_type = {"type": "both"}
    default_expired_products = {"allowSale": False}
    default_overtime = {"enabled": False, "endTime": "22:00"}
    default_sell_by_exchange_rate = True
    default_profitability = {"enabled": False, "rate": 30}
    default_invoice = {"autoPrint": False, "autoSave": True, "fontSize": 12}
    default_report = {"defaultFontSize": 12}
    default_working_hours = {
        "enabled": True,
        "startTime": "08:00",
        "endTime": "20:00",
        "overtimeEndTime": "22:00",
        "timezone": "Africa/Kinshasa",
        "daysOff": {
            "monday": True,
            "tuesday": True,
            "wednesday": True,
            "thursday": True,
            "friday": True,
            "saturday": True,
            "sunday": False
        }
    }
    default_margin_config = {
        "defaultMargin": 25,
        "minMargin": 10,
        "maxMargin": 50
    }
    default_automatic_pricing = {
        "enabled": False,
        "method": "percentage",
        "value": 25
    }
    default_branch_config = {
        "maxBranches": 1,
        "currentBranches": 0,
        "branches": []
    }
    
    for pharmacy_id, config in pharmacies:
        # Si config est None, initialiser
        if config is None:
            config = {}
        
        # Convertir en dict si c'est une string JSON
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except:
                config = {}
        
        # --- Ajouter logoUrl dans pharmacyInfo ---
        if "pharmacyInfo" not in config:
            config["pharmacyInfo"] = {}
        if "logoUrl" not in config["pharmacyInfo"]:
            config["pharmacyInfo"]["logoUrl"] = config["pharmacyInfo"].get("logo")
        
        # --- Ajouter salesType ---
        if "salesType" not in config:
            config["salesType"] = default_sales_type
        elif isinstance(config["salesType"], str):
            # Migration depuis l'ancien format (string)
            config["salesType"] = {"type": config["salesType"]}
        
        # --- Ajouter expiredProducts ---
        if "expiredProducts" not in config:
            config["expiredProducts"] = default_expired_products
        
        # --- Ajouter overtime ---
        if "overtime" not in config:
            config["overtime"] = default_overtime
        # Migration depuis overtimeEnabled
        if "overtimeEnabled" in config:
            config["overtime"]["enabled"] = config.pop("overtimeEnabled", False)
        
        # --- Ajouter sellByExchangeRate ---
        if "sellByExchangeRate" not in config:
            config["sellByExchangeRate"] = default_sell_by_exchange_rate
        
        # --- Ajouter profitability ---
        if "profitability" not in config:
            config["profitability"] = default_profitability
        
        # --- Ajouter invoice ---
        if "invoice" not in config:
            config["invoice"] = default_invoice
        
        # --- Ajouter report ---
        if "report" not in config:
            config["report"] = default_report
        
        # --- Ajouter workingHours si absent ---
        if "workingHours" not in config:
            config["workingHours"] = default_working_hours
        else:
            # S'assurer que overtimeEndTime existe
            if "overtimeEndTime" not in config["workingHours"]:
                config["workingHours"]["overtimeEndTime"] = "22:00"
        
        # --- Ajouter marginConfig si absent ---
        if "marginConfig" not in config:
            config["marginConfig"] = default_margin_config
        
        # --- Ajouter automaticPricing si absent ---
        if "automaticPricing" not in config:
            config["automaticPricing"] = default_automatic_pricing
        
        # --- Ajouter branchConfig si absent ---
        if "branchConfig" not in config:
            config["branchConfig"] = default_branch_config
        
        # Mettre à jour la date
        config["updatedAt"] = datetime.utcnow().isoformat()
        
        # Sauvegarder
        conn.execute(
            sa.text("UPDATE pharmacies SET config = :config, updated_at = :updated_at WHERE id = :id"),
            {
                "config": json.dumps(config, ensure_ascii=False),
                "updated_at": datetime.utcnow(),
                "id": str(pharmacy_id)
            }
        )
        
        updated_count += 1
    
    print(f"✅ Migration terminée : {updated_count} pharmacies mises à jour")


def downgrade() -> None:
    """
    Annule les modifications
    """
    conn = op.get_bind()
    
    # Récupérer toutes les pharmacies
    result = conn.execute(
        sa.text("SELECT id, config FROM pharmacies WHERE config IS NOT NULL")
    )
    
    pharmacies = result.fetchall()
    updated_count = 0
    
    for pharmacy_id, config in pharmacies:
        if config is None:
            continue
        
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except:
                continue
        
        # Supprimer les nouveaux champs
        fields_to_remove = [
            "salesType",
            "expiredProducts",
            "overtime",
            "sellByExchangeRate",
            "profitability",
            "invoice",
            "report"
        ]
        
        for field in fields_to_remove:
            if field in config:
                del config[field]
        
        # Supprimer logoUrl de pharmacyInfo
        if "pharmacyInfo" in config and "logoUrl" in config["pharmacyInfo"]:
            del config["pharmacyInfo"]["logoUrl"]
        
        # Mettre à jour la date
        config["updatedAt"] = datetime.utcnow().isoformat()
        
        # Sauvegarder
        conn.execute(
            sa.text("UPDATE pharmacies SET config = :config, updated_at = :updated_at WHERE id = :id"),
            {
                "config": json.dumps(config, ensure_ascii=False),
                "updated_at": datetime.utcnow(),
                "id": str(pharmacy_id)
            }
        )
        
        updated_count += 1
    
    print(f"✅ Rollback terminé : {updated_count} pharmacies restaurées")