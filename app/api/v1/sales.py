# app/api/v1/endpoints/sales.py
"""
API de gestion des ventes avec intégration complète du stock
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, desc, and_, or_, distinct, asc
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
from decimal import Decimal
from fastapi import status
from http import HTTPStatus  
from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product, ProductStock
from app.models.customer import Customer
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.user_pharmacy import UserPharmacy
from app.models.tenant import Tenant
from app.models.stock_movement import StockMovement
from app.schemas.sale import (
    SaleCreate, SaleResponse, SaleInDB, SaleUpdate, SaleFilter,
    DailyStatsResponse, SaleListResponse, QuickSaleRequest, 
    SaleRefundRequest, CreditSaleCreate, ReceiptData, SaleItemCreate, 
    SaleDetailResponse, SalesStatsResponse, PharmacyStats, SaleExportRequest, 
    SaleExportResponse, UserPharmacyAccess, SaleValidationRequest, 
    SaleItemResponse, SaleImpactResponse, PeriodStatsResponse
)
from app.schemas.customer import CustomerCreate, CustomerInDB
from app.api.deps import (
    get_current_tenant, 
    get_current_user, 
    get_current_active_user, 
    require_role, 
    require_permission,
    get_current_pharmacy_entity,
    get_current_branch_entity,
    can_user_access_pharmacy
)
from app.services.inventory import InventoryService
from app.services.reporting import ReportService
from app.services.receipt import ReceiptService
from app.core.config import settings

router = APIRouter(prefix="/sales", tags=["Ventes"])
logger = logging.getLogger(__name__)


# =======================
# Tâches en arrière-plan
# =======================

async def generate_sale_receipt(sale_id: UUID, tenant_id: UUID, pharmacy_id: UUID, db: Session):
    """Génère un reçu PDF pour la vente"""
    try:
        # Vérifier que MEDIA_ROOT est configuré
        if not hasattr(settings, 'MEDIA_ROOT') or not settings.MEDIA_ROOT:
            logger.warning(f"⚠️ MEDIA_ROOT non configuré, génération de reçu ignorée pour la vente {sale_id}")
            return
        
        # Vérifier si la génération des reçus est activée
        if not getattr(settings, 'GENERATE_RECEIPTS', True):
            logger.info(f"📄 Génération de reçus désactivée pour la vente {sale_id}")
            return
        
        receipt_service = ReceiptService(db)
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == pharmacy_id
        ).first()
        
        if sale:
            receipt_path = await receipt_service.generate_sale_receipt(sale)
            if receipt_path:
                sale.receipt_path = receipt_path
                db.commit()
                logger.info(f"✅ Reçu généré pour la vente {sale.reference}: {receipt_path}")
            else:
                logger.warning(f"⚠️ Échec génération reçu pour {sale.reference}")
                
    except Exception as e:
        logger.error(f"❌ Erreur génération reçu pour vente {sale_id}: {str(e)}")


async def update_sales_statistics(tenant_id: UUID, pharmacy_id: UUID, sale_date: date, db: Session):
    """Met à jour les statistiques de ventes"""
    try:
        report_service = ReportService(db)
        # Note: La fonction update_daily_sales_stats a été modifiée pour accepter pharmacy_id
        await report_service.update_daily_sales_stats(tenant_id, pharmacy_id, sale_date)
        logger.info(f"Statistiques mises à jour pour {sale_date} - pharmacy {pharmacy_id}")
    except Exception as e:
        logger.error(f"Erreur mise à jour statistiques: {str(e)}")


# =======================
# Helpers
# =======================

def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: Optional[UUID] = None) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if not user_id:
        return []
    
    query = db.query(UserPharmacy.pharmacy_id).filter(UserPharmacy.user_id == user_id)
    
    if tenant_id:
        query = query.join(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    
    return [p.pharmacy_id for p in query.all()]


def check_stock_availability_with_prices(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    items: List[SaleItemCreate],
    inventory_service: InventoryService
) -> tuple[List[Dict], List[Dict]]:
    """
    Vérifie la disponibilité des stocks et récupère les prix du stock.
    Retourne (items_disponibles, items_indisponibles)
    """
    available_items = []
    unavailable_items = []
    
    for item in items:
        product = db.query(Product).filter(
            Product.id == item.product_id,
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == pharmacy_id,
            Product.is_active == True
        ).first()
        
        if not product:
            unavailable_items.append({
                "product_id": str(item.product_id),
                "product_name": "Unknown",
                "requested": float(item.quantity),
                "available": 0,
                "reason": "Produit non trouvé"
            })
            continue
        
        # Vérifier le stock disponible
        available_quantity = product.available_quantity
        
        if available_quantity < item.quantity:
            unavailable_items.append({
                "product_id": str(item.product_id),
                "product_name": product.name,
                "requested": float(item.quantity),
                "available": float(available_quantity),
                "unit": product.unit,
                "reason": "Stock insuffisant"
            })
        else:
            available_items.append({
                "product": product,
                "item": item,
                "available_stock": available_quantity,
                "selling_price": product.selling_price,
                "tva_rate": product.tva_rate if product.has_tva else Decimal('0')
            })
    
    return available_items, unavailable_items


# =======================
# Routes principales
# =======================

@router.post("/", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
async def create_sale(
    sale_data: SaleCreate,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    force_invoice_number: Optional[str] = Query(None, description="Force un numéro de facture spécifique (réservé admin)")
):
    """
    Crée une nouvelle vente avec gestion complète par pharmacie
    et mise à jour automatique du stock avec traçabilité.
    
    GESTION DES NUMÉROS DE FACTURE:
    - Si invoice_number est fourni dans sale_data et qu'il est unique, il est utilisé
    - Sinon, le serveur génère automatiquement un numéro unique
    - En cas de conflit (numéro déjà existant), un nouveau numéro est généré
    
    IMPORTANT:
    - Le prix de vente est TOUJOURS celui défini dans le stock (product.selling_price)
    - Le taux de TVA est TOUJOURS celui défini dans le stock (product.tva_rate)
    - Les champs unit_price et tva_rate dans SaleItemCreate sont ignorés
    - Seule la remise (discount_percent) peut être appliquée par l'utilisateur
    """
    
    # Vérifier les permissions
    user_role = (current_user.role).lower() if current_user.role else ""
    allowed_roles = ["admin", "super_admin", "superadmin", "vendeur", "gerant", "caissier"]
    
    logger.info(f"🔍 Rôle utilisateur: {user_role}")
    
    if user_role not in allowed_roles:
        logger.error(f"❌ Rôle insuffisant: {user_role}")
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN,
            detail=f"Rôle insuffisant pour créer une vente. Rôles autorisés: {allowed_roles}, rôle actuel: {user_role}"
        )
    
    try:
        # Déterminer la pharmacie
        pharmacy = current_pharmacy
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie active sélectionnée"
            )
        
        tenant_id = current_tenant.id if current_tenant else None

        # Validation des données
        if not sale_data.items or len(sale_data.items) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La vente doit contenir au moins un article"
            )

        # VALIDATION: Vérifier qu'aucun prix n'est spécifié dans la requête
        for idx, item in enumerate(sale_data.items):
            if hasattr(item, 'unit_price') and item.unit_price is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Article {idx + 1}: Le prix de vente ne peut pas être spécifié. "
                           f"Utilisez le prix défini dans le stock."
                )
            if hasattr(item, 'tva_rate') and item.tva_rate is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Article {idx + 1}: Le taux de TVA ne peut pas être spécifié. "
                           f"Utilisez le taux défini dans le stock."
                )

        # Service d'inventaire
        inventory_service = InventoryService(db, tenant_id)

        # Vérification des stocks et récupération des produits avec leurs prix
        available_items, unavailable_items = check_stock_availability_with_prices(
            db=db,
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            items=sale_data.items,
            inventory_service=inventory_service
        )

        if unavailable_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Stock insuffisant pour certains articles",
                    "unavailable_items": unavailable_items,
                    "pharmacy": pharmacy.name
                }
            )

        # Vérification client
        client = None
        if sale_data.customer_id:
            client = db.query(Customer).filter(
                Customer.id == sale_data.customer_id,
                Customer.tenant_id == tenant_id,
                Customer.is_active == True
            ).first()
            
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client non trouvé ou inactif"
                )
            
            # Vérification crédit client
            if sale_data.is_credit:
                if not getattr(client, 'eligible_credit', False):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Client non éligible au crédit"
                    )
                
                credit_limit = getattr(client, 'credit_limit', Decimal('0'))
                current_debt = getattr(client, 'dette_actuelle', Decimal('0'))
                credit_available = credit_limit - current_debt
                
                # Calcul temporaire du total pour validation
                temp_total = Decimal('0')
                for available in available_items:
                    product = available["product"]
                    item = available["item"]
                    unit_price = product.selling_price
                    tva_rate = product.tva_rate if product.has_tva else Decimal('0')
                    
                    item_subtotal = unit_price * Decimal(str(item.quantity))
                    item_discount = item_subtotal * (Decimal(str(item.discount_percent)) / Decimal('100')) if item.discount_percent else Decimal('0')
                    item_after_discount = item_subtotal - item_discount
                    item_tva = item_after_discount * (tva_rate / Decimal('100'))
                    item_total = item_after_discount + item_tva
                    temp_total += item_total
                
                # Appliquer la remise globale
                if sale_data.global_discount:
                    global_discount_amount = temp_total * (Decimal(str(sale_data.global_discount)) / Decimal('100'))
                    temp_total -= global_discount_amount
                
                if temp_total > credit_available:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Crédit insuffisant. Disponible: {float(credit_available):.2f}, Requis: {float(temp_total):.2f}"
                    )

        # ========================
        # GESTION DU NUMÉRO DE FACTURE
        # ========================
        
        final_invoice_number = None
        
        # Vérifier si un numéro de facture a été fourni
        if sale_data.invoice_number:
            # Vérifier si ce numéro existe déjà
            existing_sale = db.query(Sale).filter(
                Sale.invoice_number == sale_data.invoice_number,
                Sale.tenant_id == tenant_id
            ).first()
            
            if existing_sale:
                # Conflit détecté - générer un nouveau numéro
                logger.warning(f"⚠️ Conflit numéro facture {sale_data.invoice_number} - Génération automatique")
                final_invoice_number = await generate_unique_invoice_number(
                    db, tenant_id, pharmacy.id
                )
            else:
                # Numéro valide et unique
                final_invoice_number = sale_data.invoice_number
                logger.info(f"📋 Utilisation du numéro facture client: {final_invoice_number}")
        else:
            # Aucun numéro fourni - génération automatique
            final_invoice_number = await generate_unique_invoice_number(
                db, tenant_id, pharmacy.id
            )
            logger.info(f"📋 Numéro facture généré automatiquement: {final_invoice_number}")

        # Génération référence vente
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        pharmacy_code = getattr(pharmacy, 'pharmacy_code', 'PHARM')
        reference = f"VNT-{timestamp}-{pharmacy_code}"

        # Calcul des totaux en utilisant les prix du stock
        subtotal = Decimal('0')
        total_discount = Decimal('0')
        total_tva = Decimal('0')
        total_amount = Decimal('0')
        
        # Stocker les données calculées pour chaque item
        items_calculated = []
        
        for available in available_items:
            product = available["product"]
            item = available["item"]
            
            # Utiliser le prix de vente du stock
            unit_price = product.selling_price
            tva_rate = product.tva_rate if product.has_tva else Decimal('0')
            discount_percent = Decimal(str(item.discount_percent)) if item.discount_percent else Decimal('0')
            
            # Calculs
            item_subtotal = unit_price * Decimal(str(item.quantity))
            item_discount = item_subtotal * (discount_percent / Decimal('100'))
            item_after_discount = item_subtotal - item_discount
            item_tva = item_after_discount * (tva_rate / Decimal('100'))
            item_total = item_after_discount + item_tva
            
            subtotal += item_subtotal
            total_discount += item_discount
            total_tva += item_tva
            total_amount += item_total
            
            # Stocker pour utilisation ultérieure
            items_calculated.append({
                "product": product,
                "item": item,
                "unit_price": unit_price,
                "tva_rate": tva_rate,
                "discount_percent": discount_percent,
                "item_subtotal": item_subtotal,
                "item_discount": item_discount,
                "item_tva": item_tva,
                "item_total": item_total
            })
        
        # Appliquer la remise globale
        global_discount_percent = Decimal(str(sale_data.global_discount)) if sale_data.global_discount else Decimal('0')
        if global_discount_percent > 0:
            global_discount_amount = total_amount * (global_discount_percent / Decimal('100'))
            total_discount += global_discount_amount
            total_amount -= global_discount_amount

        # Créer la vente avec le numéro de facture final
        sale = Sale(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy.id,
            reference=reference,
            customer_id=sale_data.customer_id,
            customer_name=client.nom_complet if client else sale_data.customer_name,
            customer_phone=client.telephone if client else sale_data.customer_phone,
            created_by=current_user.id,
            seller_name=getattr(current_user, 'nom_complet', current_user.email),
            payment_method=sale_data.payment_method.value,
            reference_payment=sale_data.reference_payment,
            payment_date=datetime.utcnow() if sale_data.payment_method.value != "credit" else None,
            is_credit=sale_data.is_credit,
            credit_due_date=sale_data.credit_due_date,
            guarantee_deposit=Decimal(str(sale_data.guarantee_deposit)) if sale_data.guarantee_deposit else Decimal('0'),
            guarantor_name=sale_data.guarantor_name,
            guarantor_phone=sale_data.guarantor_phone,
            global_discount=global_discount_percent,
            notes=sale_data.notes,
            subtotal=subtotal,
            total_discount=total_discount,
            total_tva=total_tva,
            total_amount=total_amount,
            status="pending" if sale_data.is_credit else "completed",
            invoice_number=final_invoice_number,  # ← Numéro de facture unique
            cancelled_at=None,
            cancelled_by=None,
            cancel_reason=None
        )
        
        db.add(sale)
        db.flush()

        # Création des items et mise à jour des stocks
        sale_items = []
        
        for calc in items_calculated:
            product = calc["product"]
            item = calc["item"]
            
            # Créer l'item de vente avec les prix du stock
            sale_item = SaleItem(
                sale_id=sale.id,
                tenant_id=tenant_id,
                pharmacy_id=pharmacy.id,
                product_id=item.product_id,
                product_code=product.code,
                product_name=product.name,
                quantity=item.quantity,
                unit_price=calc["unit_price"],
                discount_percent=calc["discount_percent"],
                discount_amount=calc["item_discount"],
                tva_rate=calc["tva_rate"],
                tva_amount=calc["item_tva"],
                subtotal=calc["item_subtotal"],
                total=calc["item_total"],
                batch_number=item.batch_number,
                expiry_date=item.expiry_date
            )
            db.add(sale_item)
            sale_items.append(sale_item)
            
            # Mettre à jour le stock
            old_quantity = product.quantity
            new_quantity = old_quantity - item.quantity
            
            if new_quantity < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Stock insuffisant pour {product.name}"
                )
            
            product.quantity = new_quantity
            product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
            product.total_sold = (product.total_sold or 0) + item.quantity
            product.last_sale_date = datetime.utcnow()
            product.refresh_statuses()
            
            # Créer un mouvement de stock avec traçabilité du prix utilisé
            movement = StockMovement(
                tenant_id=tenant_id,
                branch_id=getattr(pharmacy, 'branch_id', None),
                product_id=product.id,
                pharmacy_id=pharmacy.id,
                quantity_before=old_quantity,
                quantity_after=new_quantity,
                quantity_change=-item.quantity,
                movement_type="sale",
                reason="vente",
                reference=sale.reference,
                batch_number=item.batch_number,
                purchase_price=product.purchase_price,
                selling_price=calc["unit_price"],
                sale_id=sale.id,
                sale_item_id=sale_item.id,
                created_by=current_user.id
            )
            db.add(movement)

        # Mise à jour client
        if client:
            client.total_achats = (getattr(client, 'total_achats', Decimal('0')) or Decimal('0')) + total_amount
            client.nombre_achats = (getattr(client, 'nombre_achats', 0) or 0) + 1
            client.dernier_achat = datetime.utcnow()
            client.dernier_montant = total_amount
            
            if sale_data.is_credit:
                client.dette_actuelle = (getattr(client, 'dette_actuelle', Decimal('0')) or Decimal('0')) + total_amount

        # Incrémenter le compteur de factures
        await increment_invoice_counter(db, tenant_id, pharmacy.id, final_invoice_number)

        # Validation automatique si configuré
        if getattr(settings, 'AUTO_VALIDATE_SALES', False) and current_user.role in ["admin", "super_admin", "superadmin", "gerant"]:
            sale.status = "completed"
            sale.validated_by = current_user.id
            sale.validated_at = datetime.utcnow()

        db.commit()
        db.refresh(sale)

        # Tâches en arrière-plan
        background_tasks.add_task(generate_sale_receipt, sale.id, tenant_id, pharmacy.id, db)
        background_tasks.add_task(update_sales_statistics, tenant_id, pharmacy.id, datetime.now().date(), db)

        logger.info(
            f"Vente créée: {sale.reference} - Facture: {final_invoice_number} - "
            f"{len(sale_items)} articles - Pharmacie: {pharmacy.name} par {current_user.email}"
        )

        # Construction de la réponse
        return SaleResponse(
            message="Vente créée avec succès",
            sale=SaleInDB.model_validate(sale),
            pharmacy={
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "code": getattr(pharmacy, 'pharmacy_code', None)
            },
            receipt_available=True,
            receipt_url=f"/api/v1/sales/{sale.id}/receipt",
            generated_invoice_number=final_invoice_number  # Ajouter ce champ à SaleResponse
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création vente: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
            detail=f"Erreur création vente: {str(e)}"
        )


# ========================
# FONCTIONS UTILITAIRES POUR LES NUMÉROS DE FACTURE
# ========================

async def generate_unique_invoice_number(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    max_attempts: int = 5
) -> str:
    """
    Génère un numéro de facture unique pour une pharmacie.
    Format: INV-YYYYMMDD-XXXX (ex: INV-20260423-0042)
    """
    from app.models.invoice_counter import InvoiceCounter
    
    today = datetime.now().date()
    date_str = today.strftime("%Y%m%d")
    
    for attempt in range(max_attempts):
        # Récupérer ou créer le compteur
        counter = db.query(InvoiceCounter).filter(
            InvoiceCounter.tenant_id == tenant_id,
            InvoiceCounter.pharmacy_id == pharmacy_id,
            InvoiceCounter.date == today
        ).first()
        
        if not counter:
            counter = InvoiceCounter(
                tenant_id=tenant_id,
                pharmacy_id=pharmacy_id,
                date=today,
                current_number=1
            )
            db.add(counter)
            db.flush()
        
        # Générer le numéro
        sequence = counter.current_number
        invoice_number = f"INV-{date_str}-{sequence:04d}"
        
        # Vérifier l'unicité
        existing = db.query(Sale).filter(
            Sale.invoice_number == invoice_number,
            Sale.tenant_id == tenant_id
        ).first()
        
        if not existing:
            # Incrémenter le compteur pour la prochaine fois
            counter.current_number += 1
            db.flush()
            return invoice_number
        
        # Conflit - incrémenter et réessayer
        counter.current_number += 1
        logger.warning(f"⚠️ Conflit sur {invoice_number}, tentative {attempt + 2}/{max_attempts}")
    
    # Fallback avec timestamp
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"INV-{timestamp}"


async def increment_invoice_counter(
    db: Session,
    tenant_id: UUID,
    pharmacy_id: UUID,
    used_invoice_number: str
) -> None:
    """
    Incrémente le compteur après utilisation réussie d'un numéro.
    Utile pour la synchronisation avec les clients.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    # Extraire la date du numéro de facture
    try:
        parts = used_invoice_number.split('-')
        if len(parts) >= 3 and parts[0] == 'INV':
            date_str = parts[1]
            sequence = int(parts[2])
            
            # S'assurer que le compteur est au moins à séquence + 1
            today = datetime.now().date()
            counter = db.query(InvoiceCounter).filter(
                InvoiceCounter.tenant_id == tenant_id,
                InvoiceCounter.pharmacy_id == pharmacy_id,
                InvoiceCounter.date == today
            ).first()
            
            if counter and counter.current_number <= sequence:
                counter.current_number = sequence + 1
                db.flush()
    except Exception as e:
        logger.error(f"Erreur incrémentation compteur: {e}")


# ========================
# ENDPOINTS POUR SYNCHRONISATION DES FACTURES
# ========================

@router.get("/next-invoice-number")
async def get_next_invoice_number(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Récupère le prochain numéro de facture disponible.
    Les clients doivent appeler cet endpoint AVANT chaque vente en ligne.
    """
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = current_pharmacy.id if current_pharmacy else None
    
    if not pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie sélectionnée"
        )
    
    next_number = await generate_unique_invoice_number(db, tenant_id, pharmacy_id)
    
    return {
        "invoice_number": next_number,
        "pharmacy_id": str(pharmacy_id),
        "tenant_id": str(tenant_id) if tenant_id else None,
        "generated_at": datetime.utcnow().isoformat()
    }


@router.post("/sync-invoice-counter")
async def sync_invoice_counter(
    sync_data: dict,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Synchronise le compteur de factures depuis un client.
    Utile pour la reprise après mode offline.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = UUID(sync_data.get("pharmacy_id"))
    last_invoice_number = sync_data.get("last_invoice_number")
    max_sequence = sync_data.get("max_sequence", 0)
    
    today = datetime.now().date()
    
    counter = db.query(InvoiceCounter).filter(
        InvoiceCounter.tenant_id == tenant_id,
        InvoiceCounter.pharmacy_id == pharmacy_id,
        InvoiceCounter.date == today
    ).first()
    
    if not counter:
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            date=today,
            current_number=1
        )
        db.add(counter)
    
    # Prendre le maximum entre le compteur local et celui du client
    if max_sequence > counter.current_number:
        counter.current_number = max_sequence
        logger.info(f"Compteur mis à jour: {counter.current_number} (client: {max_sequence})")
    
    db.commit()
    
    return {
        "success": True,
        "new_counter": counter.current_number,
        "next_invoice": f"INV-{today.strftime('%Y%m%d')}-{counter.current_number:04d}"
    }

@router.get("/next-invoice-number")
async def get_next_invoice_number(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Récupère le prochain numéro de facture disponible depuis le serveur.
    Le client doit appeler cet endpoint avant de créer une vente en ligne.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    tenant_id = current_tenant.id if current_tenant else None
    pharmacy_id = current_pharmacy.id if current_pharmacy else None
    
    # Récupérer ou créer le compteur pour cette pharmacie
    counter = db.query(InvoiceCounter).filter(
        InvoiceCounter.tenant_id == tenant_id,
        InvoiceCounter.pharmacy_id == pharmacy_id
    ).first()
    
    if not counter:
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            current_number=1,
            last_invoice_date=datetime.now().date()
        )
        db.add(counter)
        db.commit()
        db.refresh(counter)
    
    # Vérifier si on a changé de jour
    today = datetime.now().date()
    if counter.last_invoice_date != today:
        counter.current_number = 1
        counter.last_invoice_date = today
        db.commit()
    
    next_number = counter.current_number
    date_str = today.strftime("%Y%m%d")
    pharmacy_code = getattr(current_pharmacy, 'pharmacy_code', 'PHARM')
    
    return {
        "invoice_number": f"INV-{date_str}-{next_number:04d}",
        "sequence_number": next_number,
        "date": date_str,
        "pharmacy_code": pharmacy_code
    }

@router.post("/confirm-invoice-number", status_code=status.HTTP_200_OK)
async def confirm_invoice_number(
    request: dict,  # Accepter dict pour plus de flexibilité
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
):
    """
    Confirme l'utilisation d'un numéro de facture et incrémente le compteur.
    Accepte soit invoice_number soit pharmacy_id dans le body.
    """
    from app.models.invoice_counter import InvoiceCounter
    
    # Extraire les paramètres (supporte plusieurs formats)
    invoice_number = request.get("invoice_number") or request.get("invoiceNumber")
    pharmacy_id = request.get("pharmacy_id") or request.get("pharmacyId")
    
    # Si pharmacy_id non fourni, utiliser celui de la session
    if not pharmacy_id and current_pharmacy:
        pharmacy_id = current_pharmacy.id
    elif not pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pharmacy_id requis"
        )
    
    tenant_id = current_tenant.id if current_tenant else None
    
    today = datetime.now().date()
    
    # Chercher le compteur
    counter = db.query(InvoiceCounter).filter(
        InvoiceCounter.tenant_id == tenant_id,
        InvoiceCounter.pharmacy_id == pharmacy_id,
        InvoiceCounter.date == today
    ).first()
    
    if counter:
        # Si un numéro spécifique est fourni, s'assurer que le compteur est au moins à ce niveau
        if invoice_number:
            try:
                # Extraire le numéro séquentiel de INV-20260424-0042
                parts = invoice_number.split('-')
                if len(parts) >= 3:
                    sequence = int(parts[2])
                    if counter.current_number <= sequence:
                        counter.current_number = sequence + 1
                        db.commit()
            except Exception as e:
                logger.warning(f"Erreur extraction séquence: {e}")
        else:
            # Sinon, simplement incrémenter
            counter.current_number += 1
            db.commit()
    else:
        # Créer un nouveau compteur
        if invoice_number:
            sequence = 2  # Valeur par défaut
            try:
                parts = invoice_number.split('-')
                if len(parts) >= 3:
                    sequence = int(parts[2]) + 1
            except:
                pass
        else:
            sequence = 2
            
        counter = InvoiceCounter(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            date=today,
            current_number=sequence
        )
        db.add(counter)
        db.commit()
    
    return {
        "success": True,
        "invoice_number": invoice_number,
        "new_sequence": counter.current_number,
        "pharmacy_id": str(pharmacy_id),
        "date": today.isoformat()
    }
# =======================
# Endpoint: Impact des ventes sur le stock
# =======================

@router.get("/stock-impact", response_model=List[SaleImpactResponse])
async def get_sales_stock_impact(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    product_id: Optional[UUID] = Query(None, description="Filtrer par produit"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Récupère l'impact des ventes sur le stock
    Point de communication avec le module stock
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
            if tenant_id:
                pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
            if pharmacy_id:
                pharmacies_query = pharmacies_query.filter(Pharmacy.id == pharmacy_id)
            pharmacy_ids = [p.id for p in pharmacies_query.all()]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            if pharmacy_id:
                pharmacy_ids = [pharmacy_id]
        
        if not pharmacy_ids:
            return []
        
        # Construction de la requête
        query = db.query(
            Product.id.label("product_id"),
            Product.code,
            Product.name,
            Product.unit,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
            func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
            func.count(distinct(Sale.id)).label("sale_count"),
            func.avg(SaleItem.unit_price).label("average_price")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacy_ids)
        )
        
        if tenant_id:
            query = query.filter(Product.tenant_id == tenant_id)
            query = query.filter(Sale.tenant_id == tenant_id)
        
        if product_id:
            query = query.filter(Product.id == product_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.group_by(
            Product.id, Product.code, Product.name, Product.unit
        ).order_by(
            desc("total_sold")
        ).limit(limit).all()
        
        return [
            SaleImpactResponse(
                product_id=row.product_id,
                product_code=row.code,
                product_name=row.name,
                unit=row.unit,
                total_sold=int(row.total_sold),
                total_revenue=float(row.total_revenue),
                sale_count=int(row.sale_count),
                average_price=float(row.average_price),
                stock_impact=-int(row.total_sold)
            )
            for row in results
        ]
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération impact stock: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération impact stock: {str(e)}"
        )


# =======================
# Endpoint: Mouvements de stock liés à une vente
# =======================

@router.get("/stock-movements/{sale_id}")
async def get_sale_stock_movements(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère tous les mouvements de stock liés à une vente spécifique
    Point de communication avec le module stock
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que la vente existe et est accessible
        sale_query = db.query(Sale).filter(Sale.id == sale_id)
        if tenant_id:
            sale_query = sale_query.filter(Sale.tenant_id == tenant_id)
        
        sale = sale_query.first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_user.role not in ["super_admin", "superadmin", "admin"]:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if sale.pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette vente"
                )
        
        # Récupérer les mouvements de stock liés à cette vente
        movements = db.query(StockMovement).filter(
            StockMovement.sale_id == sale_id,
            StockMovement.tenant_id == tenant_id
        ).all()
        
        return {
            "sale_id": str(sale.id),
            "sale_reference": sale.reference,
            "pharmacy_id": str(sale.pharmacy_id),
            "movements": [
                {
                    "id": str(m.id),
                    "product_id": str(m.product_id),
                    "product_name": getattr(m.product, "name", None),
                    "quantity_change": float(m.quantity_change) if m.quantity_change else 0,
                    "quantity_before": float(m.quantity_before) if m.quantity_before else 0,
                    "quantity_after": float(m.quantity_after) if m.quantity_after else 0,
                    "movement_type": m.movement_type,
                    "reason": m.reason,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "batch_number": m.batch_number
                }
                for m in movements
            ],
            "total_movements": len(movements)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération mouvements stock pour vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération mouvements stock: {str(e)}"
        )


# =======================
# Endpoint: Ventes par produit
# =======================

@router.get("/by-product/{product_id}")
async def get_sales_by_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    limit: int = Query(100, ge=1, le=1000)
):
    """
    Récupère toutes les ventes pour un produit spécifique
    Utile pour l'analyse des ventes par produit
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Vérifier que le produit existe
        product_query = db.query(Product).filter(Product.id == product_id)
        if tenant_id:
            product_query = product_query.filter(Product.tenant_id == tenant_id)
        
        product = product_query.first()
        
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produit non trouvé"
            )
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return {
                "product_id": str(product_id),
                "product_name": product.name,
                "product_code": product.code,
                "sales": [],
                "total_sales": 0,
                "total_quantity": 0,
                "total_revenue": 0
            }
        
        # Construire la requête
        query = db.query(
            Sale.id,
            Sale.reference,
            Sale.created_at,
            Sale.client_name,
            Sale.seller_name,
            SaleItem.quantity,
            SaleItem.unit_price,
            SaleItem.total,
            Sale.pharmacy_id
        ).join(
            SaleItem, SaleItem.sale_id == Sale.id
        ).filter(
            SaleItem.product_id == product_id,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies)
        )
        
        if tenant_id:
            query = query.filter(Sale.tenant_id == tenant_id)
            query = query.filter(SaleItem.tenant_id == tenant_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.order_by(desc(Sale.created_at)).limit(limit).all()
        
        total_quantity = sum(r.quantity for r in results)
        total_revenue = sum(float(r.total) for r in results)
        
        return {
            "product_id": str(product_id),
            "product_name": product.name,
            "product_code": product.code,
            "sales": [
                {
                    "sale_id": str(r.id),
                    "reference": r.reference,
                    "date": r.created_at.isoformat() if r.created_at else None,
                    "client_name": r.client_name,
                    "seller_name": r.seller_name,
                    "quantity": int(r.quantity),
                    "unit_price": float(r.unit_price),
                    "total": float(r.total),
                    "pharmacy_id": str(r.pharmacy_id) if r.pharmacy_id else None
                }
                for r in results
            ],
            "total_sales": len(results),
            "total_quantity": int(total_quantity),
            "total_revenue": float(total_revenue)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération ventes pour produit {product_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération ventes: {str(e)}"
        )


# =======================
# Endpoint: Statistiques quotidiennes
# =======================

@router.get("/stats/daily", response_model=DailyStatsResponse)
async def get_daily_stats_endpoint(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    target_date: Optional[date] = Query(None, description="Date (format YYYY-MM-DD)"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques de ventes pour une journée spécifique.
    Si la date n'est pas fournie, utilise la date du jour.
    """
    try:
        if target_date is None:
            target_date = datetime.now().date()
        
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return DailyStatsResponse(
                date=target_date.isoformat(),
                sales_count=0,
                total_amount=0,
                average_basket=0,
                items_sold=0,
                top_products=[],
                by_pharmacy=[]
            )
        
        # Date range pour la journée
        start_datetime = datetime.combine(target_date, datetime.min.time())
        end_datetime = datetime.combine(target_date, datetime.max.time())
        
        # Requête principale
        stats_query = db.query(
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount"),
            func.coalesce(func.sum(SaleItem.quantity), 0).label("items_sold"),
            func.avg(Sale.total_amount).label("average_basket")
        ).join(
            SaleItem, SaleItem.sale_id == Sale.id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            stats_query = stats_query.filter(Sale.tenant_id == tenant_id)
        
        stats_result = stats_query.first()
        
        # Top produits
        top_products_query = db.query(
            Product.id.label("product_id"),
            Product.name.label("product_name"),
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_amount")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            top_products_query = top_products_query.filter(Sale.tenant_id == tenant_id)
        
        top_products = top_products_query.group_by(
            Product.id, Product.name
        ).order_by(
            desc("total_quantity")
        ).limit(5).all()
        
        # Par pharmacie
        by_pharmacy_query = db.query(
            Pharmacy.id.label("pharmacy_id"),
            Pharmacy.name.label("pharmacy_name"),
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount")
        ).join(
            Sale, Sale.pharmacy_id == Pharmacy.id
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_datetime,
            Sale.created_at <= end_datetime
        )
        
        if tenant_id:
            by_pharmacy_query = by_pharmacy_query.filter(Sale.tenant_id == tenant_id)
        
        by_pharmacy_results = by_pharmacy_query.group_by(
            Pharmacy.id, Pharmacy.name
        ).all()
        
        total_sales = float(stats_result.total_amount or 0)
        by_pharmacy = []
        for pharm in by_pharmacy_results:
            percentage = (float(pharm.total_amount) / total_sales * 100) if total_sales > 0 else 0
            by_pharmacy.append({
                "pharmacy_id": str(pharm.pharmacy_id),
                "pharmacy_name": pharm.pharmacy_name,
                "sales_count": pharm.sales_count,
                "total_amount": float(pharm.total_amount),
                "percentage": percentage
            })
        
        return DailyStatsResponse(
            date=target_date.isoformat(),
            sales_count=stats_result.sales_count or 0,
            total_amount=float(stats_result.total_amount or 0),
            average_basket=float(stats_result.average_basket or 0),
            items_sold=int(stats_result.items_sold or 0),
            top_products=[
                {
                    "product": p.product_name,
                    "quantity": int(p.total_quantity),
                    "amount": float(p.total_amount)
                }
                for p in top_products
            ],
            by_pharmacy=by_pharmacy
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats quotidiennes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )


# =======================
# Endpoint: Statistiques globales (overview)
# =======================

@router.get("/stats/overview", response_model=SalesStatsResponse)
async def get_sales_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques globales des ventes.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = datetime.now().date()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            empty_period = {"total": 0, "count": 0, "average": 0}
            empty_daily = DailyStatsResponse(
                date=today.isoformat(),
                sales_count=0,
                total_amount=0,
                average_basket=0,
                items_sold=0,
                top_products=[],
                by_pharmacy=[]
            )
            return SalesStatsResponse(
                today=empty_daily,
                week=empty_period,
                month=empty_period,
                year=empty_period
            )
        
        def get_stats_for_period(start: datetime, end: datetime) -> Dict[str, Any]:
            query = db.query(
                func.count(distinct(Sale.id)).label("count"),
                func.coalesce(func.sum(Sale.total_amount), 0).label("total")
            ).filter(
                Sale.status == "completed",
                Sale.pharmacy_id.in_(pharmacies),
                Sale.created_at >= start,
                Sale.created_at <= end
            )
            if tenant_id:
                query = query.filter(Sale.tenant_id == tenant_id)
            result = query.first()
            total = float(result.total or 0)
            count = result.count or 0
            return {
                "total": total,
                "count": count,
                "average": total / count if count > 0 else 0
            }
        
        # Statistiques du jour
        today_stats = await get_daily_stats_endpoint(
            db=db,
            current_tenant=current_tenant,
            current_user=current_user,
            target_date=today,
            pharmacy_id=pharmacy_id
        )
        
        # Statistiques de la semaine
        week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        week_end = datetime.combine(today, datetime.max.time())
        week_stats = get_stats_for_period(week_start, week_end)
        
        # Statistiques du mois
        month_start = datetime.combine(today.replace(day=1), datetime.min.time())
        month_end = datetime.combine(today, datetime.max.time())
        month_stats = get_stats_for_period(month_start, month_end)
        
        # Statistiques de l'année
        year_start = datetime.combine(today.replace(month=1, day=1), datetime.min.time())
        year_end = datetime.combine(today, datetime.max.time())
        year_stats = get_stats_for_period(year_start, year_end)
        
        return SalesStatsResponse(
            today=today_stats,
            week=week_stats,
            month=month_stats,
            year=year_stats
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats globales: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )

# =======================
# Endpoint: Liste des ventes (avec filtres et pagination)
# =======================

@router.get("/", response_model=SaleListResponse)
async def get_sales(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    # Pagination
    skip: int = Query(0, ge=0, description="Nombre d'éléments à sauter"),
    limit: int = Query(100, ge=1, le=1000, description="Nombre d'éléments par page"),
    # Filtres
    start_date: Optional[datetime] = Query(None, description="Date de début"),
    end_date: Optional[datetime] = Query(None, description="Date de fin"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    user_id: Optional[UUID] = Query(None, description="Filtrer par utilisateur (caissier)"),
    payment_method: Optional[str] = Query(None, description="Filtrer par méthode de paiement"),
    status: Optional[str] = Query(None, description="Filtrer par statut (completed, pending, cancelled)"),
    customer_id: Optional[UUID] = Query(None, description="Filtrer par client"),
    search: Optional[str] = Query(None, description="Recherche par référence, client, vendeur"),
    # Tri
    sort_by: str = Query("created_at", description="Champ de tri (created_at, total_amount, reference)"),
    sort_order: str = Query("desc", description="Ordre de tri (asc, desc)"),
):
    """
    Récupère la liste des ventes avec filtres et pagination.
    Accessible aux administrateurs et vendeurs (limité à leurs pharmacies).
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin", "gerant"]:
            if pharmacy_id:
                # Vérifier que la pharmacie existe
                pharmacy_check = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
                if not pharmacy_check:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Pharmacie non trouvée"
                    )
                pharmacy_ids = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacy_ids = [p.id for p in pharmacies_query.all()]
        else:
            # Vendeur, caissier - uniquement ses pharmacies
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id:
                if pharmacy_id not in accessible_pharmacies:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Accès non autorisé à cette pharmacie"
                    )
                pharmacy_ids = [pharmacy_id]
            else:
                pharmacy_ids = accessible_pharmacies
        
        if not pharmacy_ids:
            return SaleListResponse(
                total=0,
                page=skip // limit + 1 if limit > 0 else 1,
                page_size=limit,
                data=[]
            )
        
        # Construction de la requête de base
        query = db.query(Sale).filter(
            Sale.pharmacy_id.in_(pharmacy_ids),
            Sale.status != "deleted"  # Exclure les ventes supprimées
        )
        
        if tenant_id:
            query = query.filter(Sale.tenant_id == tenant_id)
        
        # Filtres
        if start_date:
            query = query.filter(Sale.created_at >= start_date)
        if end_date:
            query = query.filter(Sale.created_at <= end_date)
        
        if user_id:
            query = query.filter(Sale.created_by == user_id)
        
        if payment_method:
            query = query.filter(Sale.payment_method == payment_method)
        
        if status:
            query = query.filter(Sale.status == status)
        
        if customer_id:
            query = query.filter(Sale.customer_id == customer_id)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Sale.reference.ilike(search_term),
                    Sale.client_name.ilike(search_term),
                    Sale.seller_name.ilike(search_term),
                    Sale.invoice_number.ilike(search_term)
                )
            )
        
        # Compter le total avant pagination
        total = query.count()
        
        # Appliquer le tri
        sort_column = getattr(Sale, sort_by, Sale.created_at)
        if sort_order.lower() == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(asc(sort_column))
        
        # Pagination
        sales = query.offset(skip).limit(limit).all()
        
        # Construire la réponse
        sales_data = []
        for sale in sales:
            # Récupérer les items de la vente
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
            
            # Récupérer le nom de la pharmacie
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
            
            sales_data.append(SaleInDB(
                id=sale.id,
                tenant_id=sale.tenant_id,
                pharmacy_id=sale.pharmacy_id,
                pharmacy_name=pharmacy.name if pharmacy else None,
                reference=sale.reference,
                customer_id=sale.customer_id,
                customer_name=sale.customer_name,
                customer_phone=sale.customer_phone,
                created_by=sale.created_by,
                seller_name=sale.seller_name,
                created_at=sale.created_at,
                updated_at=sale.updated_at,
                payment_method=sale.payment_method,
                reference_payment=sale.reference_payment,
                payment_date=sale.payment_date,
                is_credit=sale.is_credit,
                credit_due_date=sale.credit_due_date,
                guarantee_deposit=sale.guarantee_deposit,
                guarantor_name=sale.guarantor_name,
                guarantor_phone=sale.guarantor_phone,
                global_discount=sale.global_discount,
                notes=sale.notes,
                subtotal=float(sale.subtotal) if sale.subtotal else 0,
                total_discount=float(sale.total_discount) if sale.total_discount else 0,
                total_tva=float(sale.total_tva) if sale.total_tva else 0,
                total_amount=float(sale.total_amount) if sale.total_amount else 0,
                status=sale.status,
                validated_by=sale.validated_by,
                validated_at=sale.validated_at,
                cancelled_at=sale.cancelled_at,
                cancelled_by=sale.cancelled_by,
                cancel_reason=sale.cancel_reason,
                invoice_number=sale.invoice_number,
                receipt_path=sale.receipt_path,
                invoice_path=getattr(sale, 'invoice_path', None),
                items=[
                    SaleItemResponse(
                        id=item.id,
                        sale_id=item.sale_id,
                        tenant_id=item.tenant_id,
                        pharmacy_id=item.pharmacy_id,
                        created_at=item.created_at,
                        product_id=item.product_id,
                        product_name=item.product_name,
                        product_code=item.product_code,
                        quantity=float(item.quantity),
                        unit_price=float(item.unit_price),
                        discount_percent=float(item.discount_percent) if item.discount_percent else 0,
                        discount_amount=float(item.discount_amount) if item.discount_amount else 0,
                        tva_rate=float(item.tva_rate) if item.tva_rate else 0,
                        tva_amount=float(item.tva_amount) if item.tva_amount else 0,
                        subtotal=float(item.subtotal) if item.subtotal else 0,
                        total=float(item.total) if item.total else 0,
                        batch_number=item.batch_number,
                        expiry_date=item.expiry_date
                    )
                    for item in items
                ]
            ))
        
        return SaleListResponse(
            items=sales_data,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=len(sales_data),
            has_more=(skip + limit) < total,
            page_size=limit
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération liste des ventes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération des ventes: {str(e)}"
        )


@router.get("/{sale_id}", response_model=SaleDetailResponse)
async def get_sale_by_id(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
):
    """
    Récupère les détails d'une vente spécifique par son ID.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Récupérer la vente
        query = db.query(Sale).filter(Sale.id == sale_id)
        if tenant_id:
            query = query.filter(Sale.tenant_id == tenant_id)
        
        sale = query.first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_user.role not in ["super_admin", "superadmin", "admin", "gerant"]:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if sale.pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette vente"
                )
        
        # Récupérer les items
        items = db.query(SaleItem).filter(SaleItem.sale_id == sale.id).all()
        
        # Récupérer la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == sale.pharmacy_id).first()
        
        return SaleDetailResponse(
            id=sale.id,
            tenant_id=sale.tenant_id,
            pharmacy_id=sale.pharmacy_id,
            pharmacy_name=pharmacy.name if pharmacy else None,
            reference=sale.reference,
            customer_id=sale.customer_id,
            customer_name=sale.customer_name,
            customer_phone=sale.customer_phone,
            created_by=sale.created_by,
            seller_name=sale.seller_name,
            created_at=sale.created_at,
            updated_at=sale.updated_at,
            payment_method=sale.payment_method,
            reference_payment=sale.reference_payment,
            payment_date=sale.payment_date,
            is_credit=sale.is_credit,
            credit_due_date=sale.credit_due_date,
            guarantee_deposit=float(sale.guarantee_deposit) if sale.guarantee_deposit else 0,
            guarantor_name=sale.guarantor_name,
            guarantor_phone=sale.guarantor_phone,
            global_discount=float(sale.global_discount) if sale.global_discount else 0,
            notes=sale.notes,
            subtotal=float(sale.subtotal) if sale.subtotal else 0,
            total_discount=float(sale.total_discount) if sale.total_discount else 0,
            total_tva=float(sale.total_tva) if sale.total_tva else 0,
            total_amount=float(sale.total_amount) if sale.total_amount else 0,
            status=sale.status,
            validated_by=sale.validated_by,
            validated_at=sale.validated_at,
            cancelled_at=sale.cancelled_at,
            cancelled_by=sale.cancelled_by,
            cancel_reason=sale.cancel_reason,
            invoice_number=sale.invoice_number,
            receipt_path=sale.receipt_path,
            items=[
                SaleItemResponse(
                    id=item.id,
                    product_id=item.product_id,
                    product_name=item.product_name,
                    product_code=item.product_code,
                    quantity=float(item.quantity),
                    unit_price=float(item.unit_price),
                    discount_percent=float(item.discount_percent) if item.discount_percent else 0,
                    discount_amount=float(item.discount_amount) if item.discount_amount else 0,
                    tva_rate=float(item.tva_rate) if item.tva_rate else 0,
                    tva_amount=float(item.tva_amount) if item.tva_amount else 0,
                    subtotal=float(item.subtotal) if item.subtotal else 0,
                    total=float(item.total) if item.total else 0,
                    batch_number=item.batch_number,
                    expiry_date=item.expiry_date
                )
                for item in items
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération de la vente: {str(e)}"
        )

@property
def nom_complet(self):
    return self.full_name
# =======================
# Endpoint: Statistiques par période
# =======================

@router.get("/stats/period", response_model=PeriodStatsResponse)
async def get_period_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    period: str = Query("day", description="Période: day, week, month, year"),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie")
):
    """
    Récupère les statistiques agrégées par période.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        today = datetime.now().date()
        
        # Déterminer les pharmacies accessibles
        if current_user.role in ["super_admin", "superadmin", "admin"]:
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies_query = db.query(Pharmacy.id).filter(Pharmacy.is_active == True)
                if tenant_id:
                    pharmacies_query = pharmacies_query.filter(Pharmacy.tenant_id == tenant_id)
                pharmacies = [p.id for p in pharmacies_query.all()]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, tenant_id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return PeriodStatsResponse(
                period=period,
                start_date=datetime.now().isoformat(),
                end_date=datetime.now().isoformat(),
                data=[]
            )
        
        # Déterminer le nombre de jours à retourner
        if period == "day":
            days = 1
            group_by = func.date(Sale.created_at)
        elif period == "week":
            days = 7
            group_by = func.date_trunc('week', Sale.created_at)
        elif period == "month":
            days = 30
            group_by = func.date_trunc('month', Sale.created_at)
        elif period == "year":
            days = 365
            group_by = func.date_trunc('year', Sale.created_at)
        else:
            days = 7
            group_by = func.date(Sale.created_at)
        
        start_date = datetime.combine(today - timedelta(days=days), datetime.min.time())
        
        # Requête
        results = db.query(
            group_by.label("date"),
            func.count(distinct(Sale.id)).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_amount"),
            func.avg(Sale.total_amount).label("average_basket")
        ).filter(
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies),
            Sale.created_at >= start_date
        )
        
        if tenant_id:
            results = results.filter(Sale.tenant_id == tenant_id)
        
        results = results.group_by("date").order_by("date").all()
        
        return PeriodStatsResponse(
            period=period,
            start_date=start_date.isoformat(),
            end_date=datetime.now().isoformat(),
            data=[
                {
                    "date": r.date.isoformat() if r.date else None,
                    "sales_count": r.sales_count,
                    "total_amount": float(r.total_amount),
                    "average_basket": float(r.average_basket or 0)
                }
                for r in results
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération stats par période: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération stats: {str(e)}"
        )


# =======================
# Endpoint de test
# =======================

@router.get("/test", include_in_schema=False)
async def test_sales(
    current_user: User = Depends(get_current_active_user)
):
    """
    Endpoint de test
    """
    return {
        "message": "Module Ventes avec Pharmacies opérationnel",
        "version": "3.1.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Gestion complète des ventes par pharmacie",
            "Multi-pharmacies pour les admin",
            "Contrôle d'accès par pharmacie",
            "Statistiques par pharmacie",
            "Gestion des stocks par pharmacie",
            "Communication bidirectionnelle avec le module stock",
            "Traçabilité complète des mouvements de stock"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }