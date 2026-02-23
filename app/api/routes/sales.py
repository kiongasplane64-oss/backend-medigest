# app/api/routes/sales.py (version complète et corrigée)
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from uuid import UUID
from datetime import datetime
import logging

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.client import Client
from app.models.user import User
from app.schemas.sale import (
    SaleCreate, SaleResponse, SaleInDB, SaleUpdate, SaleFilter,
    SaleStats, SaleListResponse, QuickSaleRequest, SaleRefundRequest,
    CreditSaleCreate, ReceiptData, SaleItemCreate
)
from app.schemas.client import ClientCreate, ClientInDB
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.inventory import InventoryService
from app.services.reporting import ReportService

router = APIRouter(prefix="/sales", tags=["Ventes"])
logger = logging.getLogger(__name__)

# --- Fonctions de tâches en arrière-plan ---
def generate_sale_receipt(sale_id: UUID, tenant_id: UUID):
    """
    Fonction fictive pour générer un reçu en arrière-plan.
    À remplacer par ton implémentation réelle.
    """
    logger.info(f"Reçu généré pour la vente {sale_id} du tenant {tenant_id}")

def send_sale_notification(sale_id: UUID, tenant_id: UUID):
    """
    Fonction fictive pour envoyer une notification en arrière-plan.
    À remplacer par ton implémentation réelle.
    """
    logger.info(f"Notification envoyée pour la vente {sale_id} du tenant {tenant_id}")

# --- Routes principales ---
@router.post("/", response_model=SaleResponse)
@require_permission("gestion_ventes")
def create_sale(
    sale_data: SaleCreate,
    db: Session = Depends(get_db),
    current_tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """
    Crée une nouvelle vente avec gestion d'inventaire en temps réel
    """
    try:
        # Vérifier la limite de crédit si vente à crédit
        if sale_data.is_credit and sale_data.client_id:
            client = db.query(Client).filter(
                Client.id == sale_data.client_id,
                Client.tenant_id == current_tenant.id
            ).first()
            
            if not client or not client.eligible_credit:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Client non éligible au crédit"
                )
            
            total_amount = sale_data.total_amount
            if client.dette_actuelle + total_amount > client.credit_limit:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Limite de crédit dépassée. Dette actuelle: {client.dette_actuelle}, Limite: {client.credit_limit}"
                )
        
        # Vérifier les stocks pour tous les articles
        inventory_service = InventoryService(db, current_tenant.id)
        inventory_updates = []
        
        for item in sale_data.items:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == current_tenant.id
            ).first()
            
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Produit {item.product_id} non trouvé"
                )
            
            if product.quantite < item.quantity:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stock insuffisant pour {product.nom}. Disponible: {product.quantite}"
                )
            
            if product.date_peremption and product.date_peremption < datetime.now().date():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Produit {product.nom} périmé depuis le {product.date_peremption}"
                )
        
        reference = f"VNT-{datetime.now().strftime('%Y%m%d')}-{UUID().hex[:8].upper()}"
        
        sale = Sale(
            tenant_id=current_tenant.id,
            reference=reference,
            client_id=sale_data.client_id,
            client_name=sale_data.client_name,
            client_phone=sale_data.client_phone,
            payment_method=sale_data.payment_method.value,
            reference_payment=sale_data.reference_payment,
            seller_id=current_user.id,
            seller_name=current_user.nom_complet,
            is_credit=sale_data.is_credit,
            credit_due_date=sale_data.credit_due_date,
            global_discount=sale_data.global_discount,
            notes=sale_data.notes,
            subtotal=sale_data.subtotal,
            total_discount=sale_data.total_discount,
            total_tva=sale_data.total_tva,
            total_amount=sale_data.total_amount,
            status="complete" if not sale_data.is_credit else "pending"
        )
        
        db.add(sale)
        db.flush()  # Pour obtenir l'ID
        
        for item in sale_data.items:
            sale_item = SaleItem(
                sale_id=sale.id,
                product_id=item.product_id,
                product_code=item.product_code,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_percent=item.discount_percent,
                tva_rate=item.tva_rate,
                subtotal=item.subtotal,
                tva_amount=item.tva_amount,
                total=item.total
            )
            db.add(sale_item)
            
            update = inventory_service.update_stock(
                product_id=item.product_id,
                quantity_change=-item.quantity,
                reason="sale",
                reference=sale.reference
            )
            inventory_updates.append(update)
        
        if sale_data.client_id:
            client = db.query(Client).filter(
                Client.id == sale_data.client_id,
                Client.tenant_id == current_tenant.id
            ).first()
            if client:
                client.total_achats += sale_data.total_amount
                client.nombre_achats += 1
                client.moyenne_achat = client.total_achats / client.nombre_achats
                client.dernier_achat = datetime.utcnow()
                if sale_data.is_credit:
                    client.dette_actuelle += sale_data.total_amount - (sale_data.guarantee_deposit or 0)
        
        db.commit()
        db.refresh(sale)
        
        if background_tasks:
            background_tasks.add_task(generate_sale_receipt, sale.id, current_tenant.id)
            background_tasks.add_task(send_sale_notification, sale.id, current_tenant.id)
        
        logger.info(f"Vente créée: {sale.reference} - Montant: {sale.total_amount} - Vendeur: {current_user.nom_complet}")
        
        return SaleResponse(
            message="Vente enregistrée avec succès",
            sale=sale,
            inventory_updates=inventory_updates
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création de la vente: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'enregistrement de la vente"
        )
