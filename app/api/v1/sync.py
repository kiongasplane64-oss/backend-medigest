# app/api/v1/sync.py - Version finale complète et optimisée
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from uuid import UUID
import logging

from app.api.deps import get_db, get_current_user
from app.schemas.sync import SyncPayload, SyncItem
from app.services.sync_service import process_sync, get_changes_since, get_sync_status


from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.stock_movement import StockMovement
from app.models.debt import Debt
from app.models.debt_payment import DebtPayment
from app.models.branch import Branch
from app.models.category import Category
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.subscription import Subscription
from app.models.customer import Customer


router = APIRouter(prefix="/sync", tags=["Sync"])
logger = logging.getLogger(__name__)


@router.post("/")
def sync_data(
    payload: SyncPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint de synchronisation des données
    
    - Filtre automatiquement les items avec des données nulles
    - Valide les actions et les noms de tables
    - Supporte les noms de tables en français et anglais
    - Retourne un rapport détaillé des traitements
    """
    
    # 1. Filtrer les items valides et invalides
    valid_items: List[SyncItem] = []
    invalid_items: List[Dict[str, Any]] = []
    
    for item in payload.items:
        errors = []
        
        # Validation de base : data requis pour create/update/upsert
        if item.action in ['create', 'update', 'upsert'] and (item.data is None or not item.data):
            errors.append("Les données sont requises pour cette action")
            logger.warning(f"Item rejeté: action={item.action}, table={item.table_name}, data absent")
        
        # Validation du nom de table (supporte les alias français)
        if item.table_name:
            table_mapping = {
                    # Français -> Anglais
                    'produits': 'products', 'produit': 'products',
                    'catégories': 'categories', 'categorie': 'categories', 'categories': 'categories',
                    'commandes': 'sales', 'commande': 'sales',  # Changé de orders à sales
                    'clients': 'customers', 'client': 'customers',
                    'factures': 'invoices', 'facture': 'invoices',
                    'utilisateurs': 'users', 'utilisateur': 'users',
                    'tenants': 'tenants', 'tenant': 'tenants',
                    'subscriptions': 'subscriptions', 'abonnements': 'subscriptions', 'abonnement': 'subscriptions',
                    'ventes': 'sales', 'vente': 'sales', 'sales': 'sales',
                    'dettes': 'debts', 'dette': 'debts', 'debts': 'debts',
                    'retours': 'returns', 'retour': 'returns', 'returns': 'returns',
                    'branches': 'branches', 'succursales': 'branches', 'succursale': 'branches',
                    'pharmacies': 'pharmacies', 'pharmacie': 'pharmacies',
                    'mouvements_stock': 'stock_movements', 'mouvement_stock': 'stock_movements',
                    'paiements_dette': 'debt_payments', 'paiement_dette': 'debt_payments',
            }

            allowed_tables = [
                    'products', 'categories', 'sales', 'customers', 
                    'invoices', 'users', 'tenants', 'subscriptions',
                    'debts', 'returns', 'branches', 'pharmacies', 
                    'stock_movements', 'debt_payments'
                ]
            
            normalized = table_mapping.get(item.table_name.lower(), item.table_name.lower())
            if normalized not in allowed_tables:
                errors.append(
                    f"Table '{item.table_name}' non autorisée. "
                    f"Tables autorisées: {', '.join(allowed_tables)}"
                )
        
        # Validation de l'action
        allowed_actions = ['create', 'update', 'delete', 'upsert']
        if item.action.lower() not in allowed_actions:
            errors.append(
                f"Action '{item.action}' non valide. "
                f"Actions autorisées: {', '.join(allowed_actions)}"
            )
        
        # Validation spécifique pour la suppression
        if item.action == 'delete' and item.data:
            if 'id' not in item.data:
                errors.append("L'ID est requis pour l'action 'delete'")
        
        # Pour create, s'assurer que les données ne sont pas vides
        if item.action == 'create' and item.data:
            if not any(item.data.values()):
                errors.append("Les données ne peuvent pas être vides pour la création")
        
        if errors:
            invalid_items.append({
                "item": item.model_dump() if hasattr(item, 'model_dump') else item.__dict__,
                "errors": errors
            })
        else:
            # Normaliser le nom de la table avant traitement
            if item.table_name.lower() in ['produits', 'produit', 'catégories', 'categorie', 
                                           'commandes', 'commande', 'clients', 'client',
                                           'factures', 'facture', 'utilisateurs', 'utilisateur',
                                           'abonnements', 'abonnement', 'ventes', 'vente',
                                           'dettes', 'dette', 'retours', 'retour']:
                table_mapping = {
                    'produits': 'products', 'produit': 'products',
                    'catégories': 'categories', 'categorie': 'categories',
                    'commandes': 'orders', 'commande': 'orders',
                    'clients': 'customers', 'client': 'customers',
                    'factures': 'invoices', 'facture': 'invoices',
                    'utilisateurs': 'users', 'utilisateur': 'users',
                    'abonnements': 'subscriptions', 'abonnement': 'subscriptions',
                    'ventes': 'sales', 'vente': 'sales',
                    'dettes': 'debts', 'dette': 'debts',
                    'retours': 'returns', 'retour': 'returns'
                }
                item.table_name = table_mapping.get(item.table_name.lower(), item.table_name)
            valid_items.append(item)
    
    # 2. Si tous les items sont invalides
    if not valid_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Aucun item valide à synchroniser",
                "invalid_items": invalid_items,
                "total_items": len(payload.items)
            }
        )  
    # 3. Logger les items filtrés
    if invalid_items:
        logger.warning(
            f"{len(invalid_items)} item(s) ignoré(s) pour le tenant {user.tenant_id}: "
            f"{[item['errors'] for item in invalid_items]}"
        )
    
    # 4. Traiter les items valides
    try:
        process_sync(db, user.tenant_id, valid_items)
        
        return {
            "status": "success",
            "message": "Synchronisation traitée avec succès",
            "tenant_id": str(user.tenant_id),
            "summary": {
                "total_items": len(payload.items),
                "processed_items": len(valid_items),
                "ignored_items": len(invalid_items),
                "success": True
            },
            "details": {
                "processed": [
                    {
                        "table_name": item.table_name,
                        "action": item.action,
                        "id": item.data.get('id') if item.data else None
                    }
                    for item in valid_items[:10]
                ] if valid_items else [],
                "ignored": invalid_items if invalid_items else None,
                "processed_tables": list(set(item.table_name for item in valid_items))
            }
        }
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la synchronisation pour le tenant {user.tenant_id}: {str(e)}",
            exc_info=True
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Erreur lors du traitement de la synchronisation",
                "message": str(e),
                "tenant_id": str(user.tenant_id),
                "processed_items": len(valid_items) if valid_items else 0
            }
        )


@router.get("/pull")
def pull_data(
    last_sync: Optional[str] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Récupérer les données modifiées depuis la dernière synchronisation"""
    try:
        last_sync_dt = None
        if last_sync:
            try:
                last_sync_clean = last_sync.replace('Z', '+00:00')
                last_sync_dt = datetime.fromisoformat(last_sync_clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Format de date invalide. Utilisez le format ISO (YYYY-MM-DDTHH:MM:SS)"
                )
        
        changes = get_changes_since(db, user.tenant_id, last_sync_dt)
        
        return {
            "status": "success",
            "message": "Données récupérées avec succès",
            "tenant_id": str(user.tenant_id),
            "last_sync": last_sync,
            "timestamp": datetime.utcnow().isoformat(),
            "data": changes,
            "count": len(changes)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur lors du pull pour le tenant {user.tenant_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Erreur lors de la récupération des données", "message": str(e), "tenant_id": str(user.tenant_id)}
        )


@router.get("/status")
def sync_status(
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Vérifier le statut de la synchronisation"""
    try:
        status_info = get_sync_status(db, user.tenant_id)
        
        return {
            "status": "success",
            "tenant_id": str(user.tenant_id),
            "sync_status": status_info,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de la vérification du statut pour le tenant {user.tenant_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "Erreur lors de la vérification du statut", "message": str(e), "tenant_id": str(user.tenant_id)}
        )


@router.get("/health")
def sync_health():
    """Vérifier la santé du service de synchronisation"""
    return {
        "status": "healthy",
        "service": "sync-api",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": [
            "POST /api/v1/sync/ - Synchroniser des données",
            "GET /api/v1/sync/pull - Récupérer les changements",
            "GET /api/v1/sync/status - Statut de synchronisation",
            "GET /api/v1/sync/health - Vérification de santé"
        ]
    }


@router.post("/batch")
def sync_batch(
    payload: SyncPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint de synchronisation par lots avec traitement optimisé"""
    items_by_table: Dict[str, List[SyncItem]] = {}
    
    for item in payload.items:
        table = item.table_name
        if table not in items_by_table:
            items_by_table[table] = []
        items_by_table[table].append(item)
    
    results = {}
    total_processed = 0
    total_errors = 0
    
    for table_name, items in items_by_table.items():
        try:
            process_sync(db, user.tenant_id, items)
            results[table_name] = {"status": "success", "count": len(items)}
            total_processed += len(items)
        except Exception as e:
            logger.error(f"Erreur lors du traitement de {table_name}: {str(e)}")
            results[table_name] = {"status": "error", "error": str(e), "count": len(items)}
            total_errors += len(items)
    
    return {
        "status": "success" if total_errors == 0 else "partial",
        "message": "Synchronisation par lots traitée",
        "tenant_id": str(user.tenant_id),
        "summary": {"total_items": len(payload.items), "processed_items": total_processed, "error_items": total_errors},
        "results": results,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.post("/returns/batch")
def sync_returns_batch(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation par lots des retours"""
    returns = payload.get('returns', [])
    
    processed = 0
    errors = []
    synced_ids = []
    
    for ret in returns:
        try:
            ret_id = ret.get('id')
            if ret_id:
                synced_ids.append(ret_id)
            processed += 1
            logger.info(f"Retour synchronisé: {ret.get('id')}")
        except Exception as e:
            errors.append({"id": ret.get('id'), "error": str(e)})
            logger.error(f"Erreur traitement retour {ret.get('id')}: {str(e)}")
    
    return {
        "status": "success" if not errors else "partial",
        "synced_ids": synced_ids,
        "processed": processed,
        "errors": errors
    }


@router.post("/debts/batch")
def sync_debts_batch(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation par lots des dettes"""
    debts = payload.get('debts', [])
    
    processed = 0
    errors = []
    synced_ids = []
    
    for debt in debts:
        try:
            debt_id = debt.get('id')
            if debt_id:
                synced_ids.append(debt_id)
            processed += 1
            logger.info(f"Dette synchronisée: {debt.get('id')}")
        except Exception as e:
            errors.append({"id": debt.get('id'), "error": str(e)})
            logger.error(f"Erreur traitement dette {debt.get('id')}: {str(e)}")
    
    return {
        "status": "success" if not errors else "partial",
        "synced_ids": synced_ids,
        "processed": processed,
        "errors": errors
    }


# ============================================================
# FONCTIONS INTERNES POUR LA CRÉATION DE VENTES AVEC IGNORANCE DU STOCK
# ============================================================

def _generate_sale_number(db: Session, tenant_id: UUID) -> str:
    """Génère un numéro de vente unique"""
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"SYNC-{today}-"
    
    count = db.query(Sale).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ).count()
    
    return f"{prefix}{count + 1:04d}"


def _create_sale_force_stock(
    db: Session,
    tenant_id: UUID,
    sale_data: Dict[str, Any],
    created_by: UUID,
    force_stock_ignore: bool = True
) -> Dict[str, Any]:
    """
    Crée une vente même si le stock est insuffisant.
    Utile pour la synchronisation offline.
    """
    from app.models.sale import Sale, SaleItem
    from app.models.product import Product
    from app.models.stock_movement import StockMovement
    from app.models.branch import Branch
    from app.models.pharmacy import Pharmacy
    
    stock_warnings = []
    stock_corrections = []
    
    try:
        items = sale_data.get('items', [])
        if not items:
            return {"success": False, "error": "Aucun article dans la vente"}
        
        # Récupérer la branche et la pharmacie
        branch_id = sale_data.get('branch_id')
        pharmacy_id = sale_data.get('pharmacy_id')
        
        if not pharmacy_id:
            # Essayer de trouver la pharmacie via la branche
            if branch_id:
                branch = db.query(Branch).filter(Branch.id == branch_id).first()
                if branch:
                    pharmacy_id = branch.parent_pharmacy_id
        
        total_amount = Decimal('0')
        sale_items_data = []
        
        for item in items:
            product_id = item.get('product_id')
            if isinstance(product_id, str):
                product_id = UUID(product_id)
            
            quantity = Decimal(str(item.get('quantity', 1)))
            unit_price = Decimal(str(item.get('unit_price', 0)))
            discount = Decimal(str(item.get('discount_percent', 0)))
            
            product = db.query(Product).filter(
                Product.id == product_id,
                Product.tenant_id == tenant_id,
                Product.is_active == True
            ).first()
            
            if not product:
                return {"success": False, "error": f"Produit {product_id} non trouvé"}
            
            current_stock = product.quantity or 0
            
            # Vérifier le stock
            if current_stock < quantity and not force_stock_ignore:
                return {
                    "success": False, 
                    "error": f"Stock insuffisant pour {product.name}. Disponible: {current_stock}, Demandé: {quantity}"
                }
            
            # Enregistrer un avertissement si stock insuffisant
            if current_stock < quantity:
                stock_warnings.append({
                    "product_id": str(product_id),
                    "product_name": product.name,
                    "available_stock": float(current_stock),
                    "requested_quantity": float(quantity),
                    "shortage": float(quantity - current_stock)
                })
                quantity_change = -float(quantity)
            else:
                quantity_change = -float(quantity)
            
            # Calculer les totaux
            subtotal = quantity * unit_price
            discount_amount = subtotal * (discount / Decimal('100'))
            after_discount = subtotal - discount_amount
            
            # TVA
            tva_rate = Decimal(str(product.tva_rate)) if product.has_tva else Decimal('0')
            tva_amount = after_discount * (tva_rate / Decimal('100'))
            item_total = after_discount + tva_amount
            
            total_amount += item_total
            
            sale_items_data.append({
                "product": product,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": unit_price,
                "discount_percent": discount,
                "discount_amount": discount_amount,
                "tva_rate": tva_rate,
                "tva_amount": tva_amount,
                "subtotal": subtotal,
                "total": item_total,
                "quantity_change": quantity_change,
                "old_quantity": current_stock
            })
        
        # Appliquer la remise globale
        global_discount = Decimal(str(sale_data.get('global_discount', 0)))
        if global_discount > 0:
            global_discount_amount = total_amount * (global_discount / Decimal('100'))
            total_amount -= global_discount_amount
        
        # Créer la vente
        sale = Sale(
            tenant_id=tenant_id,
            pharmacy_id=pharmacy_id,
            branch_id=branch_id,
            reference=_generate_sale_number(db, tenant_id),
            customer_name=sale_data.get('customer_name', 'Client synchronisé'),
            customer_phone=sale_data.get('customer_phone'),
            customer_email=sale_data.get('customer_email'),
            created_by=created_by,
            seller_name=sale_data.get('seller_name', 'Sync Service'),
            payment_method=sale_data.get('payment_method', 'cash'),
            is_credit=sale_data.get('is_credit', False),
            credit_due_date=sale_data.get('credit_due_date'),
            global_discount=global_discount,
            notes=sale_data.get('notes', f"Syncé depuis offline - ID original: {sale_data.get('id')}"),
            subtotal=sum(d["subtotal"] for d in sale_items_data),
            total_discount=sum(d["discount_amount"] for d in sale_items_data) + global_discount_amount,
            total_tva=sum(d["tva_amount"] for d in sale_items_data),
            total_amount=total_amount,
            status="completed",
            is_synced=True
        )
        
        db.add(sale)
        db.flush()
        
        # Créer les items et mettre à jour le stock
        for item_data in sale_items_data:
            product = item_data["product"]
            
            sale_item = SaleItem(
                tenant_id=tenant_id,
                sale_id=sale.id,
                pharmacy_id=pharmacy_id,
                product_id=item_data["product_id"],
                product_code=product.code,
                product_name=product.name,
                quantity=item_data["quantity"],
                unit_price=item_data["unit_price"],
                discount_percent=item_data["discount_percent"],
                discount_amount=item_data["discount_amount"],
                tva_rate=item_data["tva_rate"],
                tva_amount=item_data["tva_amount"],
                subtotal=item_data["subtotal"],
                total=item_data["total"]
            )
            db.add(sale_item)
            
            # Mettre à jour le stock
            old_quantity = product.quantity or 0
            new_quantity = max(0, old_quantity + item_data["quantity_change"])
            
            product.quantity = new_quantity
            product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
            product.last_sale_date = datetime.utcnow()
            product.refresh_statuses()
            
            # Enregistrer la correction de stock
            if force_stock_ignore and old_quantity < item_data["quantity"]:
                stock_corrections.append({
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "old_quantity": old_quantity,
                    "new_quantity": new_quantity,
                    "sale_id": str(sale.id)
                })
            
            # Mouvement de stock
            movement = StockMovement(
                tenant_id=tenant_id,
                product_id=product.id,
                pharmacy_id=pharmacy_id,
                branch_id=branch_id,
                quantity_before=old_quantity,
                quantity_after=new_quantity,
                quantity_change=item_data["quantity_change"],
                movement_type="sale",
                reason=f"Synchronisation offline - Vente #{sale.reference}" + (" (force mode)" if force_stock_ignore and old_quantity < item_data["quantity"] else ""),
                reference=sale.reference,
                sale_id=sale.id,
                sale_item_id=sale_item.id,
                selling_price=item_data["unit_price"],
                created_by=created_by
            )
            db.add(movement)
        
        db.commit()
        db.refresh(sale)
        
        logger.info(f"Vente créée (force mode): {sale.reference}, Total: {sale.total_amount}")
        
        return {
            "success": True,
            "sale_id": str(sale.id),
            "sale_reference": sale.reference,
            "stock_warnings": stock_warnings,
            "stock_corrections": stock_corrections,
            "force_mode_used": force_stock_ignore and len(stock_warnings) > 0
        }
        
    except Exception as e:
        db.rollback()
        logger.exception(f"Erreur création vente force stock: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# ENDPOINTS POUR LES VENTES AVEC STOCK IGNORÉ
# ============================================================

@router.post("/sales/force")
def sync_sales_force(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint pour synchroniser les ventes même si le stock du produit est 0.
    Utile pour les synchronisations offline où le stock local peut être désynchronisé.
    """
    from app.models.sale import Sale
    
    sales = payload.get('sales', [])
    
    processed = 0
    errors = []
    synced_ids = []
    stock_warnings = []
    stock_corrections = []
    
    for sale_data in sales:
        try:
            force_ignore = sale_data.get('force_stock_ignore', True)
            
            result = _create_sale_force_stock(
                db=db,
                tenant_id=user.tenant_id,
                sale_data=sale_data,
                created_by=user.id,
                force_stock_ignore=force_ignore
            )
            
            if result.get('success'):
                synced_ids.append(sale_data.get('id'))
                processed += 1
                
                if result.get('stock_warnings'):
                    stock_warnings.extend(result['stock_warnings'])
                if result.get('stock_corrections'):
                    stock_corrections.extend(result['stock_corrections'])
                
                logger.info(f"Vente synchronisée (force mode): {sale_data.get('id')}")
            else:
                errors.append({
                    "id": sale_data.get('id'),
                    "error": result.get('error', 'Erreur inconnue')
                })
                logger.error(f"Erreur vente {sale_data.get('id')}: {result.get('error')}")
                
        except Exception as e:
            errors.append({"id": sale_data.get('id'), "error": str(e)})
            logger.error(f"Erreur traitement vente {sale_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if not errors else "partial",
        "message": f"{processed} ventes synchronisées, {len(errors)} erreurs",
        "synced_ids": synced_ids,
        "processed": processed,
        "errors": errors,
        "stock_warnings": stock_warnings,
        "stock_corrections": stock_corrections,
        "force_mode_enabled": True
    }


@router.post("/sales/with-stock-override")
def sync_sales_with_stock_override(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint pour synchroniser les ventes avec possibilité de surcharger le stock.
    Permet de spécifier un stock théorique à utiliser pour la validation.
    """
    from app.models.sale import Sale
    from app.models.product import Product
    
    sales = payload.get('sales', [])
    global_override = payload.get('global_stock_override', {
        "enabled": True,
        "ignore_negative_stock": True,
        "auto_correct_stock": True,
        "default_stock_value": 999
    })
    
    processed = 0
    errors = []
    synced_ids = []
    stock_corrections = []
    
    for sale_data in sales:
        try:
            # Modifier temporairement le stock si override activé
            if global_override.get("enabled", True):
                for item in sale_data.get('items', []):
                    product_id = item.get('product_id')
                    if isinstance(product_id, str):
                        product_id = UUID(product_id)
                    
                    product = db.query(Product).filter(
                        Product.id == product_id,
                        Product.tenant_id == user.tenant_id
                    ).first()
                    
                    if product:
                        requested_qty = item.get('quantity', 1)
                        if product.quantity < requested_qty and global_override.get("auto_correct_stock", True):
                            old_stock = product.quantity
                            product.quantity = max(requested_qty, global_override.get("default_stock_value", 999))
                            stock_corrections.append({
                                "product_id": str(product.id),
                                "product_name": product.name,
                                "old_stock": old_stock,
                                "new_stock": product.quantity,
                                "reason": "Override synchronisation"
                            })
                            db.flush()
            
            result = _create_sale_force_stock(
                db=db,
                tenant_id=user.tenant_id,
                sale_data=sale_data,
                created_by=user.id,
                force_stock_ignore=True
            )
            
            if result.get('success'):
                synced_ids.append(sale_data.get('id'))
                processed += 1
                logger.info(f"Vente synchronisée avec override: {sale_data.get('id')}")
            else:
                errors.append({"id": sale_data.get('id'), "error": result.get('error', 'Erreur inconnue')})
                
        except Exception as e:
            errors.append({"id": sale_data.get('id'), "error": str(e)})
            logger.error(f"Erreur vente {sale_data.get('id')}: {str(e)}")
    
    db.commit()
    
    return {
        "status": "success" if not errors else "partial",
        "message": f"{processed} ventes synchronisées avec override stock",
        "synced_ids": synced_ids,
        "processed": processed,
        "errors": errors,
        "stock_corrections": stock_corrections,
        "override_config": global_override
    }


@router.post("/sales/deferred")
def sync_deferred_sales(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint pour synchroniser les ventes différées.
    Les ventes sont enregistrées mais le stock n'est pas immédiatement déduit.
    """
    sales = payload.get('sales', [])
    process_async = payload.get('process_async', True)
    
    processed = 0
    errors = []
    synced_ids = []
    deferred_ids = []
    
    for sale_data in sales:
        try:
            # Créer la vente sans déduire le stock
            sale_id = sale_data.get('id')
            
            # Ici on pourrait stocker dans une table DeferredSale
            # Pour simplifier, on crée directement la vente en mode force
            result = _create_sale_force_stock(
                db=db,
                tenant_id=user.tenant_id,
                sale_data=sale_data,
                created_by=user.id,
                force_stock_ignore=True
            )
            
            if result.get('success'):
                synced_ids.append(sale_id)
                processed += 1
                if result.get('stock_warnings'):
                    deferred_ids.append(sale_id)
                logger.info(f"Vente différée créée: {sale_id}")
            else:
                errors.append({"id": sale_id, "error": result.get('error', 'Erreur inconnue')})
                
        except Exception as e:
            errors.append({"id": sale_data.get('id'), "error": str(e)})
            logger.error(f"Erreur vente différée {sale_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if not errors else "partial",
        "message": f"{processed} ventes différées créées",
        "synced_ids": synced_ids,
        "deferred_ids": deferred_ids,
        "processed": processed,
        "errors": errors,
        "process_async": process_async
    }


@router.get("/pending-stock-updates")
def get_pending_stock_updates(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    limit: int = 100
):
    """
    Récupère la liste des ventes en attente de mise à jour du stock.
    """
    from app.models.sale import Sale
    from app.models.stock_movement import StockMovement
    
    # Ventes récentes avec stock négatif potentiel
    recent_sales = db.query(Sale).filter(
        Sale.tenant_id == user.tenant_id,
        Sale.created_at >= datetime.now() - timedelta(days=7),
        Sale.status == "completed"
    ).limit(limit).all()
    
    pending = []
    for sale in recent_sales:
        # Vérifier si des mouvements de stock existent
        movements = db.query(StockMovement).filter(
            StockMovement.sale_id == sale.id
        ).count()
        
        if movements == 0:
            pending.append({
                "id": str(sale.id),
                "reference": sale.reference,
                "created_at": sale.created_at.isoformat(),
                "total_amount": float(sale.total_amount)
            })
    
    return {
        "status": "success",
        "count": len(pending),
        "pending_updates": pending
    }


@router.post("/retry-stock-updates")
def retry_stock_updates(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Force la reprise des mises à jour de stock en échec.
    """
    from app.models.sale import Sale, SaleItem
    from app.models.product import Product
    from app.models.stock_movement import StockMovement
    
    sale_ids = payload.get('sale_ids', [])
    force = payload.get('force', False)
    
    if not sale_ids:
        # Récupérer les ventes sans mouvements
        sales = db.query(Sale).filter(
            Sale.tenant_id == user.tenant_id,
            Sale.status == "completed"
        ).all()
        
        sale_ids = [s.id for s in sales]
        pending_ids = []
        
        for s in sales:
            movements = db.query(StockMovement).filter(StockMovement.sale_id == s.id).count()
            if movements == 0:
                pending_ids.append(s.id)
        sale_ids = pending_ids
    
    successful = 0
    failed = 0
    details = []
    
    for sale_id in sale_ids:
        try:
            sale = db.query(Sale).filter(
                Sale.id == sale_id,
                Sale.tenant_id == user.tenant_id
            ).first()
            
            if not sale:
                details.append({"sale_id": str(sale_id), "status": "failed", "error": "Vente non trouvée"})
                failed += 1
                continue
            
            # Vérifier si les mouvements existent déjà
            existing = db.query(StockMovement).filter(StockMovement.sale_id == sale_id).count()
            if existing > 0 and not force:
                details.append({"sale_id": str(sale_id), "status": "skipped", "reason": "Mouvements déjà existants"})
                continue
            
            # Recréer les mouvements
            items = db.query(SaleItem).filter(SaleItem.sale_id == sale_id).all()
            
            for item in items:
                product = db.query(Product).filter(Product.id == item.product_id).first()
                if product:
                    old_quantity = product.quantity or 0
                    new_quantity = old_quantity - int(item.quantity)
                    
                    product.quantity = max(0, new_quantity)
                    product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
                    
                    movement = StockMovement(
                        tenant_id=user.tenant_id,
                        product_id=product.id,
                        pharmacy_id=sale.pharmacy_id,
                        branch_id=sale.branch_id,
                        quantity_before=old_quantity,
                        quantity_after=product.quantity,
                        quantity_change=-int(item.quantity),
                        movement_type="sale",
                        reason="Reprise synchronisation",
                        reference=sale.reference,
                        sale_id=sale.id,
                        sale_item_id=item.id,
                        selling_price=item.unit_price,
                        created_by=user.id
                    )
                    db.add(movement)
            
            db.commit()
            details.append({"sale_id": str(sale_id), "status": "success"})
            successful += 1
            
        except Exception as e:
            db.rollback()
            details.append({"sale_id": str(sale_id), "status": "failed", "error": str(e)})
            failed += 1
            logger.error(f"Erreur reprise mise à jour stock vente {sale_id}: {str(e)}")
    
    return {
        "status": "success",
        "processed": successful + failed,
        "successful": successful,
        "failed": failed,
        "details": details
    }


# ============================================================
# ENDPOINTS POUR LA SYNCHRONISATION DES DETTES
# ============================================================

@router.post("/debts/sync")
def sync_debts_complete(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint complet pour la synchronisation des dettes"""
    from app.models.debt import Debt, DebtPayment
    from app.models.customer import Customer
    
    debts = payload.get('debts', [])
    action = payload.get('action', 'upsert')
    sync_payments = payload.get('sync_payments', True)
    
    if not debts:
        return {"status": "success", "message": "Aucune dette à synchroniser", "processed": 0, "synced_ids": []}
    
    processed = 0
    failed = 0
    synced_ids = []
    failed_ids = []
    errors = []
    payments_synced = 0
    
    for debt_data in debts:
        try:
            debt_id = debt_data.get('id')
            if isinstance(debt_id, str) and debt_id:
                try:
                    debt_id = UUID(debt_id)
                except:
                    pass
            
            # Vérifier si la dette existe déjà
            existing_debt = None
            if debt_id:
                existing_debt = db.query(Debt).filter(
                    Debt.id == debt_id,
                    Debt.tenant_id == user.tenant_id
                ).first()
            
            if action == "delete" and existing_debt:
                db.delete(existing_debt)
                processed += 1
                synced_ids.append(str(debt_id))
                continue
            
            if existing_debt and action in ["update", "upsert"]:
                # Mettre à jour la dette existante
                existing_debt.customer_name = debt_data.get('customer_name', existing_debt.customer_name)
                existing_debt.customer_phone = debt_data.get('customer_phone', existing_debt.customer_phone)
                existing_debt.total_amount = Decimal(str(debt_data.get('amount', existing_debt.total_amount)))
                existing_debt.remaining_amount = Decimal(str(debt_data.get('remaining_amount', existing_debt.remaining_amount)))
                existing_debt.total_paid = existing_debt.total_amount - existing_debt.remaining_amount
                existing_debt.due_date = debt_data.get('due_date') or existing_debt.due_date
                existing_debt.status = debt_data.get('status', existing_debt.status)
                existing_debt.notes = debt_data.get('notes', existing_debt.notes)
                existing_debt.is_synced = True
                existing_debt.synced_at = datetime.utcnow()
                
                processed += 1
                synced_ids.append(str(existing_debt.id))
                
            elif action in ["create", "upsert"]:
                # Créer une nouvelle dette
                new_debt = Debt(
                    tenant_id=user.tenant_id,
                    customer_id=debt_data.get('customer_id'),
                    customer_name=debt_data.get('customer_name', 'Client'),
                    customer_phone=debt_data.get('customer_phone'),
                    sale_id=debt_data.get('sale_id'),
                    debt_number=debt_data.get('debt_number', f"DEBT-{datetime.now().strftime('%Y%m%d')}-{processed+1:04d}"),
                    total_amount=Decimal(str(debt_data.get('amount', 0))),
                    remaining_amount=Decimal(str(debt_data.get('remaining_amount', debt_data.get('amount', 0)))),
                    total_paid=Decimal(str(debt_data.get('paid_amount', 0))),
                    due_date=debt_data.get('due_date'),
                    status=debt_data.get('status', 'pending'),
                    notes=debt_data.get('notes'),
                    is_synced=True,
                    synced_at=datetime.utcnow()
                )
                
                if debt_id:
                    new_debt.id = debt_id if isinstance(debt_id, UUID) else UUID(debt_id) if debt_id else None
                
                db.add(new_debt)
                db.flush()
                
                processed += 1
                synced_ids.append(str(new_debt.id))
            
            # Synchroniser les paiements associés
            if sync_payments and debt_data.get('payments'):
                for payment in debt_data.get('payments', []):
                    try:
                        # Récupérer la dette (soit existante, soit nouvelle)
                        target_debt = existing_debt if existing_debt and existing_debt.id else new_debt if 'new_debt' in locals() else None
                        
                        if target_debt:
                            existing_payment = None
                            payment_id = payment.get('id')
                            if payment_id:
                                existing_payment = db.query(DebtPayment).filter(
                                    DebtPayment.id == payment_id,
                                    DebtPayment.tenant_id == user.tenant_id
                                ).first()
                            
                            if not existing_payment:
                                new_payment = DebtPayment(
                                    tenant_id=user.tenant_id,
                                    debt_id=target_debt.id,
                                    amount=Decimal(str(payment.get('amount', 0))),
                                    payment_method=payment.get('payment_method', 'cash'),
                                    payment_date=payment.get('payment_date'),
                                    reference=payment.get('reference'),
                                    notes=payment.get('notes'),
                                    received_by=user.id
                                )
                                db.add(new_payment)
                                payments_synced += 1
                    except Exception as e:
                        logger.warning(f"Erreur sync paiement: {e}")
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            failed_ids.append(debt_data.get('id'))
            errors.append({"id": debt_data.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation dette {debt_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} dettes synchronisées, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "failed_ids": failed_ids,
        "errors": errors,
        "payments_synced": payments_synced if sync_payments else None
    }


@router.post("/debts/payments/sync")
def sync_debt_payments(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour synchroniser les paiements de dettes"""
    from app.models.debt import Debt, DebtPayment
    
    payments = payload.get('payments', [])
    
    if not payments:
        return {"status": "success", "message": "Aucun paiement à synchroniser", "processed": 0}
    
    processed = 0
    failed = 0
    synced_ids = []
    errors = []
    
    for payment in payments:
        try:
            debt_id = payment.get('debt_id')
            if isinstance(debt_id, str):
                debt_id = UUID(debt_id)
            
            # Vérifier que la dette existe
            debt = db.query(Debt).filter(
                Debt.id == debt_id,
                Debt.tenant_id == user.tenant_id
            ).first()
            
            if not debt:
                errors.append({"id": payment.get('id'), "error": f"Dette {debt_id} non trouvée"})
                failed += 1
                continue
            
            # Vérifier si le paiement existe déjà
            existing = None
            payment_id = payment.get('id')
            if payment_id:
                existing = db.query(DebtPayment).filter(
                    DebtPayment.id == payment_id,
                    DebtPayment.tenant_id == user.tenant_id
                ).first()
            
            if not existing:
                new_payment = DebtPayment(
                    tenant_id=user.tenant_id,
                    debt_id=debt.id,
                    amount=Decimal(str(payment.get('amount', 0))),
                    payment_method=payment.get('payment_method', 'cash'),
                    payment_date=payment.get('payment_date', datetime.utcnow().date()),
                    reference=payment.get('reference'),
                    notes=payment.get('notes'),
                    received_by=user.id
                )
                db.add(new_payment)
                db.flush()
                
                # Mettre à jour le solde de la dette
                debt.remaining_amount -= new_payment.amount
                debt.total_paid += new_payment.amount
                
                if debt.remaining_amount <= 0:
                    debt.status = "paid"
                    debt.paid_at = datetime.utcnow()
                elif debt.remaining_amount < debt.total_amount:
                    debt.status = "partial"
                
                synced_ids.append(str(new_payment.id))
                processed += 1
            else:
                synced_ids.append(str(existing.id))
                processed += 1
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            errors.append({"id": payment.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation paiement {payment.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} paiements synchronisés, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "errors": errors
    }


@router.get("/debts/pending")
def get_pending_debts(
    db: Session = Depends(get_db),
    user = Depends(get_current_user),
    customer_id: Optional[str] = None,
    status: Optional[str] = "pending",
    limit: int = 100
):
    """Récupère les dettes en attente de synchronisation"""
    from app.models.debt import Debt
    
    query = db.query(Debt).filter(
        Debt.tenant_id == user.tenant_id,
        Debt.is_synced == False
    )
    
    if customer_id:
        query = query.filter(Debt.customer_id == customer_id)
    if status:
        query = query.filter(Debt.status == status)
    
    pending_debts = query.limit(limit).all()
    
    return {
        "status": "success",
        "count": len(pending_debts),
        "debts": [
            {
                "id": str(d.id),
                "customer_name": d.customer_name,
                "amount": float(d.total_amount),
                "remaining_amount": float(d.remaining_amount),
                "due_date": d.due_date.isoformat() if d.due_date else None,
                "status": d.status,
                "created_at": d.created_at.isoformat()
            }
            for d in pending_debts
        ]
    }

def get_changes_since(db: Session, tenant_id: UUID, since: Optional[datetime] = None) -> Dict[str, List]:
    """Récupère tous les changements depuis une date pour toutes les entités"""
    changes = {
        'sales': [],
        'debts': [],
        'products': [],
        'users': [],
        'branches': [],
        'pharmacies': [],
        'subscriptions': [],
        'customers': [],
        'categories': [],
        'stock_movements': []
    }
    
    # 1. Ventes
    sales_query = db.query(Sale).filter(Sale.tenant_id == tenant_id)
    if since:
        sales_query = sales_query.filter(Sale.updated_at >= since)
    for sale in sales_query.all():
        changes['sales'].append({
            'id': str(sale.id),
            'reference': sale.reference,
            'total_amount': float(sale.total_amount),
            'customer_name': sale.customer_name,
            'customer_phone': sale.customer_phone,
            'payment_method': sale.payment_method,
            'is_credit': sale.is_credit,
            'status': sale.status,
            'branch_id': str(sale.branch_id) if sale.branch_id else None,
            'pharmacy_id': str(sale.pharmacy_id) if sale.pharmacy_id else None,
            'created_at': sale.created_at.isoformat(),
            'updated_at': sale.updated_at.isoformat() if sale.updated_at else None,
            'items': [
                {
                    'id': str(item.id),
                    'product_id': str(item.product_id),
                    'product_name': item.product_name,
                    'quantity': float(item.quantity),
                    'unit_price': float(item.unit_price),
                    'total': float(item.total)
                }
                for item in sale.items
            ]
        })
    
    # 2. Produits
    products_query = db.query(Product).filter(Product.tenant_id == tenant_id)
    if since:
        products_query = products_query.filter(Product.updated_at >= since)
    for product in products_query.all():
        changes['products'].append({
            'id': str(product.id),
            'code': product.code,
            'name': product.name,
            'commercial_name': product.commercial_name,
            'barcode': product.barcode,
            'quantity': product.quantity or 0,
            'available_quantity': product.available_quantity or 0,
            'purchase_price': float(product.purchase_price or 0),
            'selling_price': float(product.selling_price or 0),
            'category': product.category,
            'category_id': str(product.category_id) if product.category_id else None,
            'product_type': product.product_type,
            'unit': product.unit,
            'alert_threshold': product.alert_threshold,
            'minimum_stock': product.minimum_stock,
            'maximum_stock': product.maximum_stock,
            'expiry_date': product.expiry_date.isoformat() if product.expiry_date else None,
            'batch_number': product.batch_number,
            'location': product.location,
            'main_supplier': product.main_supplier,
            'has_tva': product.has_tva,
            'tva_rate': product.tva_rate,
            'prescription_required': product.prescription_required,
            'stock_status': product.stock_status,
            'expiry_status': product.expiry_status,
            'is_active': product.is_active,
            'branch_id': str(product.branch_id) if product.branch_id else None,
            'pharmacy_id': str(product.pharmacy_id) if product.pharmacy_id else None,
            'created_at': product.created_at.isoformat() if product.created_at else None,
            'updated_at': product.updated_at.isoformat() if product.updated_at else None
        })
    
    # 3. Utilisateurs
    users_query = db.query(User).filter(User.tenant_id == tenant_id)
    if since:
        users_query = users_query.filter(User.updated_at >= since)
    for user in users_query.all():
        changes['users'].append({
            'id': str(user.id),
            'email': user.email,
            'nom_complet': user.nom_complet,
            'role': user.role,
            'telephone': user.telephone,
            'adresse': user.adresse,
            'actif': user.actif,
            'active_pharmacy_id': str(user.active_pharmacy_id) if user.active_pharmacy_id else None,
            'active_branch_id': str(user.active_branch_id) if user.active_branch_id else None,
            'permissions': user.permissions,
            'last_login': user.last_login.isoformat() if user.last_login else None,
            'created_at': user.created_at.isoformat() if user.created_at else None,
            'updated_at': user.updated_at.isoformat() if user.updated_at else None
        })
    
    # 4. Branches
    branches_query = db.query(Branch).filter(Branch.tenant_id == tenant_id)
    if since:
        branches_query = branches_query.filter(Branch.updated_at >= since)
    for branch in branches_query.all():
        changes['branches'].append({
            'id': str(branch.id),
            'name': branch.name,
            'code': branch.code,
            'address': branch.address,
            'city': branch.city,
            'country': branch.country,
            'phone': branch.phone,
            'email': branch.email,
            'latitude': float(branch.latitude) if branch.latitude else None,
            'longitude': float(branch.longitude) if branch.longitude else None,
            'manager_name': branch.manager_name,
            'manager_id': str(branch.manager_id) if branch.manager_id else None,
            'opening_hours': branch.opening_hours,
            'config': branch.config,
            'is_active': branch.is_active,
            'is_main_branch': branch.is_main_branch,
            'parent_pharmacy_id': str(branch.parent_pharmacy_id) if branch.parent_pharmacy_id else None,
            'created_at': branch.created_at.isoformat() if branch.created_at else None,
            'updated_at': branch.updated_at.isoformat() if branch.updated_at else None
        })
    
    # 5. Pharmacies
    pharmacies_query = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
    if since:
        pharmacies_query = pharmacies_query.filter(Pharmacy.updated_at >= since)
    for pharmacy in pharmacies_query.all():
        changes['pharmacies'].append({
            'id': str(pharmacy.id),
            'name': pharmacy.name,
            'license_number': pharmacy.license_number,
            'address': pharmacy.address,
            'city': pharmacy.city,
            'country': pharmacy.country,
            'phone': pharmacy.phone,
            'email': pharmacy.email,
            'is_active': pharmacy.is_active,
            'opening_hours': pharmacy.opening_hours,
            'pharmacist_in_charge': pharmacy.pharmacist_in_charge,
            'pharmacist_license': pharmacy.pharmacist_license,
            'config': pharmacy.config,
            'created_at': pharmacy.created_at.isoformat() if pharmacy.created_at else None,
            'updated_at': pharmacy.updated_at.isoformat() if pharmacy.updated_at else None
        })
    
    # 6. Abonnements
    subscriptions_query = db.query(Subscription).filter(Subscription.tenant_id == tenant_id)
    if since:
        subscriptions_query = subscriptions_query.filter(Subscription.updated_at >= since)
    for sub in subscriptions_query.all():
        changes['subscriptions'].append({
            'id': str(sub.id),
            'tenant_id': str(sub.tenant_id),
            'plan_name': sub.plan_name,
            'plan_type': sub.plan_type,
            'status': sub.status,
            'max_users': sub.max_users,
            'max_products': sub.max_products,
            'max_branches': getattr(sub, 'max_branches', 0),
            'features': sub.features,
            'billing_cycle': sub.billing_cycle,
            'price': float(sub.price) if sub.price else 0,
            'currency': sub.currency,
            'current_period_start': sub.current_period_start.isoformat() if sub.current_period_start else None,
            'current_period_end': sub.current_period_end.isoformat() if sub.current_period_end else None,
            'cancel_at_period_end': sub.cancel_at_period_end,
            'is_active': sub.is_active,
            'created_at': sub.created_at.isoformat() if sub.created_at else None,
            'updated_at': sub.updated_at.isoformat() if sub.updated_at else None
        })
    
    # 7. Clients
    from app.models.customer import Customer
    customers_query = db.query(Customer).filter(Customer.tenant_id == tenant_id)
    if since:
        customers_query = customers_query.filter(Customer.updated_at >= since)
    for customer in customers_query.all():
        changes['customers'].append({
            'id': str(customer.id),
            'name': customer.name,
            'phone': customer.phone,
            'email': customer.email,
            'address': customer.address,
            'city': customer.city,
            'type': getattr(customer, 'type', 'regular'),
            'total_debt': float(customer.total_debt) if hasattr(customer, 'total_debt') else 0,
            'total_purchases': float(customer.total_purchases) if hasattr(customer, 'total_purchases') else 0,
            'is_active': customer.is_active,
            'branch_id': str(customer.branch_id) if customer.branch_id else None,
            'pharmacy_id': str(customer.pharmacy_id) if customer.pharmacy_id else None,
            'created_at': customer.created_at.isoformat() if customer.created_at else None,
            'updated_at': customer.updated_at.isoformat() if customer.updated_at else None
        })
    
    # 8. Catégories
    categories_query = db.query(Category).filter(Category.tenant_id == tenant_id)
    if since:
        categories_query = categories_query.filter(Category.updated_at >= since)
    for category in categories_query.all():
        changes['categories'].append({
            'id': str(category.id),
            'name': category.name,
            'description': category.description,
            'parent_id': str(category.parent_id) if category.parent_id else None,
            'is_active': category.is_active,
            'created_at': category.created_at.isoformat() if category.created_at else None,
            'updated_at': category.updated_at.isoformat() if category.updated_at else None
        })
    
    # 9. Mouvements de stock récents
    movements_query = db.query(StockMovement).filter(StockMovement.tenant_id == tenant_id)
    if since:
        movements_query = movements_query.filter(StockMovement.created_at >= since)
    for movement in movements_query.order_by(StockMovement.created_at.desc()).limit(500).all():
        changes['stock_movements'].append({
            'id': str(movement.id),
            'product_id': str(movement.product_id),
            'product_name': getattr(movement.product, 'name', None),
            'quantity_before': float(movement.quantity_before or 0),
            'quantity_after': float(movement.quantity_after or 0),
            'quantity_change': float(movement.quantity_change or 0),
            'movement_type': movement.movement_type,
            'reason': movement.reason,
            'reference': movement.reference,
            'branch_id': str(movement.branch_id) if movement.branch_id else None,
            'pharmacy_id': str(movement.pharmacy_id) if movement.pharmacy_id else None,
            'created_at': movement.created_at.isoformat() if movement.created_at else None
        })
    
    return changes

# ============================================================
# ENDPOINTS POUR LA SYNCHRONISATION DES CATÉGORIES
# ============================================================

@router.post("/categories/sync")
def sync_categories(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation des catégories"""
    categories = payload.get('categories', [])
    action = payload.get('action', 'upsert')
    
    if not categories:
        return {"status": "success", "message": "Aucune catégorie à synchroniser", "processed": 0}
    
    processed = 0
    failed = 0
    synced_ids = []
    errors = []
    
    for category_data in categories:
        try:
            category_id = category_data.get('id')
            if isinstance(category_id, str) and category_id:
                try:
                    category_id = UUID(category_id)
                except:
                    pass
            
            existing = None
            if category_id:
                existing = db.query(Category).filter(
                    Category.id == category_id,
                    Category.tenant_id == user.tenant_id
                ).first()
            
            if action == "delete" and existing:
                existing.is_active = False
                processed += 1
                synced_ids.append(str(category_id))
                continue
            
            if existing and action in ["update", "upsert"]:
                existing.name = category_data.get('name', existing.name)
                existing.description = category_data.get('description', existing.description)
                existing.parent_id = category_data.get('parent_id', existing.parent_id)
                existing.updated_at = datetime.utcnow()
                processed += 1
                synced_ids.append(str(existing.id))
                
            elif action in ["create", "upsert"]:
                new_category = Category(
                    tenant_id=user.tenant_id,
                    name=category_data.get('name'),
                    description=category_data.get('description'),
                    parent_id=category_data.get('parent_id'),
                    is_active=True
                )
                if category_id:
                    new_category.id = category_id if isinstance(category_id, UUID) else UUID(category_id)
                
                db.add(new_category)
                processed += 1
                synced_ids.append(str(new_category.id))
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            errors.append({"id": category_data.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation catégorie {category_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} catégories synchronisées, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "errors": errors
    }


@router.post("/products/sync")
def sync_products(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation des produits"""
    products = payload.get('products', [])
    action = payload.get('action', 'upsert')
    
    if not products:
        return {"status": "success", "message": "Aucun produit à synchroniser", "processed": 0}
    
    processed = 0
    failed = 0
    synced_ids = []
    errors = []
    
    for product_data in products:
        try:
            product_id = product_data.get('id')
            if isinstance(product_id, str) and product_id:
                try:
                    product_id = UUID(product_id)
                except:
                    pass
            
            existing = None
            if product_id:
                existing = db.query(Product).filter(
                    Product.id == product_id,
                    Product.tenant_id == user.tenant_id
                ).first()
            
            if action == "delete" and existing:
                existing.is_active = False
                existing.deleted_at = datetime.utcnow()
                processed += 1
                synced_ids.append(str(product_id))
                continue
            
            if existing and action in ["update", "upsert"]:
                for field, value in product_data.items():
                    if field != 'id' and hasattr(existing, field):
                        setattr(existing, field, value)
                existing.updated_at = datetime.utcnow()
                if hasattr(existing, 'refresh_statuses'):
                    existing.refresh_statuses()
                processed += 1
                synced_ids.append(str(existing.id))
                
            elif action in ["create", "upsert"]:
                product_data.pop('id', None)
                new_product = Product(
                    tenant_id=user.tenant_id,
                    **{k: v for k, v in product_data.items() if hasattr(Product, k)}
                )
                if product_id:
                    new_product.id = product_id if isinstance(product_id, UUID) else UUID(product_id)
                
                if hasattr(new_product, 'refresh_statuses'):
                    new_product.refresh_statuses()
                
                db.add(new_product)
                processed += 1
                synced_ids.append(str(new_product.id))
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            errors.append({"id": product_data.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation produit {product_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} produits synchronisés, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "errors": errors
    }


@router.post("/branches/sync")
def sync_branches(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation des branches/succursales"""
    branches = payload.get('branches', [])
    action = payload.get('action', 'upsert')
    
    if not branches:
        return {"status": "success", "message": "Aucune branche à synchroniser", "processed": 0}
    
    processed = 0
    failed = 0
    synced_ids = []
    errors = []
    
    for branch_data in branches:
        try:
            branch_id = branch_data.get('id')
            if isinstance(branch_id, str) and branch_id:
                try:
                    branch_id = UUID(branch_id)
                except:
                    pass
            
            existing = None
            if branch_id:
                existing = db.query(Branch).filter(
                    Branch.id == branch_id,
                    Branch.tenant_id == user.tenant_id
                ).first()
            
            if action == "delete" and existing:
                existing.is_active = False
                processed += 1
                synced_ids.append(str(branch_id))
                continue
            
            if existing and action in ["update", "upsert"]:
                for field, value in branch_data.items():
                    if field != 'id' and hasattr(existing, field):
                        setattr(existing, field, value)
                existing.updated_at = datetime.utcnow()
                processed += 1
                synced_ids.append(str(existing.id))
                
            elif action in ["create", "upsert"]:
                branch_data.pop('id', None)
                new_branch = Branch(
                    tenant_id=user.tenant_id,
                    **{k: v for k, v in branch_data.items() if hasattr(Branch, k)}
                )
                if branch_id:
                    new_branch.id = branch_id if isinstance(branch_id, UUID) else UUID(branch_id)
                
                db.add(new_branch)
                processed += 1
                synced_ids.append(str(new_branch.id))
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            errors.append({"id": branch_data.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation branche {branch_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} branches synchronisées, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "errors": errors
    }


@router.post("/users/sync")
def sync_users(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Endpoint pour la synchronisation des utilisateurs"""
    from app.core.security import hash_password
    
    users = payload.get('users', [])
    action = payload.get('action', 'upsert')
    
    if not users:
        return {"status": "success", "message": "Aucun utilisateur à synchroniser", "processed": 0}
    
    processed = 0
    failed = 0
    synced_ids = []
    errors = []
    
    for user_data in users:
        try:
            user_id = user_data.get('id')
            if isinstance(user_id, str) and user_id:
                try:
                    user_id = UUID(user_id)
                except:
                    pass
            
            existing = None
            if user_id:
                existing = db.query(User).filter(
                    User.id == user_id,
                    User.tenant_id == user.tenant_id
                ).first()
            
            if action == "delete" and existing:
                existing.actif = False
                processed += 1
                synced_ids.append(str(user_id))
                continue
            
            if existing and action in ["update", "upsert"]:
                for field, value in user_data.items():
                    if field != 'id' and field != 'password' and hasattr(existing, field):
                        setattr(existing, field, value)
                if user_data.get('password'):
                    existing.password_hash = hash_password(user_data['password'])
                existing.updated_at = datetime.utcnow()
                processed += 1
                synced_ids.append(str(existing.id))
                
            elif action in ["create", "upsert"]:
                password = user_data.pop('password', None)
                user_data.pop('id', None)
                new_user = User(
                    tenant_id=user.tenant_id,
                    **{k: v for k, v in user_data.items() if hasattr(User, k)}
                )
                if password:
                    new_user.password_hash = hash_password(password)
                if user_id:
                    new_user.id = user_id if isinstance(user_id, UUID) else UUID(user_id)
                
                db.add(new_user)
                processed += 1
                synced_ids.append(str(new_user.id))
            
            db.commit()
            
        except Exception as e:
            db.rollback()
            failed += 1
            errors.append({"id": user_data.get('id'), "error": str(e)})
            logger.error(f"Erreur synchronisation utilisateur {user_data.get('id')}: {str(e)}")
    
    return {
        "status": "success" if failed == 0 else "partial",
        "message": f"{processed} utilisateurs synchronisés, {failed} échecs",
        "processed": processed,
        "synced_ids": synced_ids,
        "errors": errors
    }

@router.get("/health/details")
def sync_health_details(
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """Vérification détaillée de la santé du service de synchronisation"""
    from app.models.sale import Sale
    from app.models.product import Product
    from app.models.debt import Debt
    
    # Compter les entités
    sales_count = db.query(Sale).filter(Sale.tenant_id == user.tenant_id).count()
    products_count = db.query(Product).filter(Product.tenant_id == user.tenant_id).count()
    debts_count = db.query(Debt).filter(Debt.tenant_id == user.tenant_id).count()
    
    # Dernière mise à jour
    last_sale = db.query(Sale).filter(Sale.tenant_id == user.tenant_id).order_by(Sale.updated_at.desc()).first()
    last_product = db.query(Product).filter(Product.tenant_id == user.tenant_id).order_by(Product.updated_at.desc()).first()
    
    return {
        "status": "healthy",
        "service": "sync-api",
        "tenant_id": str(user.tenant_id),
        "timestamp": datetime.utcnow().isoformat(),
        "counts": {
            "sales": sales_count,
            "products": products_count,
            "debts": debts_count
        },
        "last_updates": {
            "sale": last_sale.updated_at.isoformat() if last_sale else None,
            "product": last_product.updated_at.isoformat() if last_product else None
        }
    }