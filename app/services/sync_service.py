# app/services/sync_service.py - Service de synchronisation complet unifié

from sqlalchemy.orm import Session, joinedload
from typing import List, Dict, Any, Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from uuid import UUID
import logging

logger = logging.getLogger(__name__)


# ==========================================================
# FONCTIONS PRINCIPALES DE SYNCHRONISATION
# ==========================================================

def process_sync(
    db: Session,
    tenant_id: UUID,
    items: List[Any],
    user_branch_id: Optional[UUID] = None,
) -> Dict[str, Any]:
    """
    Traite les données de synchronisation envoyées par un client mobile.
    Supporte: products, categories, customers, sales, debts, users, branches, 
              pharmacies, subscriptions, stock_movements
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        items: Liste des items à synchroniser
        
    Returns:
        Dict contenant le statut, le nombre d'items traités et les erreurs
    """
    processed = 0
    errors = []
    results_by_table = {}
    
    for item in items:
        # Gestion des objets et dictionnaires
        if hasattr(item, 'table_name'):
            table_name = item.table_name
            action = item.action
            data = item.data if hasattr(item, 'data') else {}
        else:
            table_name = item.get("table_name")
            action = item.get("action")
            data = item.get("data", {})
        
        # Validation minimale
        if not table_name or not action:
            errors.append({
                "item": item,
                "error": "table_name ou action manquant",
            })
            continue
        
        logger.info(f"Traitement de {table_name} - {action} pour tenant {tenant_id}")
        
        try:
            result = None
            
            # Dispatch vers le handler approprié
            if table_name in ['products', 'produits', 'produit']:
                result = _sync_product(db, tenant_id, action, data)
            elif table_name in ['categories', 'categories', 'categorie']:
                result = _sync_category(db, tenant_id, action, data)
            elif table_name in ['customers', 'clients', 'client']:
                result = _sync_customer(db, tenant_id, action, data)
            elif table_name in ['sales', 'ventes', 'vente', 'orders', 'commandes', 'commande']:
                result = _sync_sale(db, tenant_id, action, data)
            elif table_name in ['debts', 'dettes', 'dette']:
                result = _sync_debt(db, tenant_id, action, data)
            elif table_name in ['debt_payments', 'paiements_dette']:
                result = _sync_debt_payment(db, tenant_id, action, data)
            elif table_name in ['users', 'utilisateurs', 'utilisateur']:
                result = _sync_user(db, tenant_id, action, data)
            elif table_name in ['branches', 'succursales', 'succursale']:
                result = _sync_branch(db, tenant_id, action, data)
            elif table_name in ['pharmacies', 'pharmacie']:
                result = _sync_pharmacy(db, tenant_id, action, data)
            elif table_name in ['subscriptions', 'abonnements', 'abonnement']:
                result = _sync_subscription(db, tenant_id, action, data)
            elif table_name in ['stock_movements', 'mouvements_stock']:
                result = _sync_stock_movement(db, tenant_id, action, data)
            else:
                logger.warning(f"Table non supportée: {table_name}")
                errors.append({
                    "table": table_name,
                    "action": action,
                    "error": f"Table non supportée: {table_name}"
                })
                continue
            
            if result and result.get('success'):
                processed += 1
                if table_name not in results_by_table:
                    results_by_table[table_name] = []
                results_by_table[table_name].append(result)
            elif result and not result.get('success'):
                errors.append({
                    "table": table_name,
                    "action": action,
                    "error": result.get('error', 'Erreur inconnue'),
                    "data": data.get('id') if data else None
                })
                
        except Exception as e:
            logger.error(f"Erreur lors du traitement de {table_name}: {str(e)}", exc_info=True)
            errors.append({
                "table": table_name,
                "action": action,
                "error": str(e),
                "data": data.get('id') if data else None
            })
            db.rollback()
    
    if processed > 0:
        db.commit()
    
    return {
        "status": "success" if not errors else "partial",
        "processed": processed,
        "errors": errors,
        "results_by_table": results_by_table,
        "synced_at": datetime.utcnow().isoformat(),
    }


def get_changes_since(
    db: Session,
    tenant_id: UUID,
    since: Optional[datetime] = None,
    branch_id: Optional[UUID] = None, 
    user_id: Optional[UUID] = None
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Récupère tous les changements depuis une date pour toutes les entités.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        since: Date de dernière synchronisation (optionnel)
        
    Returns:
        Dictionnaire contenant les changements par table
    """
    changes = {
        'products': [],
        'categories': [],
        'customers': [],
        'sales': [],
        'debts': [],
        'debt_payments': [],
        'users': [],
        'branches': [],
        'pharmacies': [],
        'subscriptions': [],
        'stock_movements': []
    }
    
    try:
        # 1. Produits
        from app.models.product import Product
        query = db.query(Product).filter(Product.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Product.updated_at >= since) | (Product.created_at >= since)
            )
        
        for product in query.all():
            changes['products'].append({
                'id': str(product.id),
                'action': 'update' if (product.updated_at and since and product.updated_at >= since) else 'create',
                'data': product.to_dict(include_details=True) if hasattr(product, 'to_dict') else _serialize_product(product),
                'timestamp': (product.updated_at or product.created_at).isoformat() if (product.updated_at or product.created_at) else None
            })
        
        # 2. Catégories
        from app.models.category import Category
        query = db.query(Category).filter(Category.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Category.updated_at >= since) | (Category.created_at >= since)
            )
        
        for category in query.all():
            changes['categories'].append({
                'id': str(category.id),
                'action': 'update' if (category.updated_at and since and category.updated_at >= since) else 'create',
                'data': {
                    'id': str(category.id),
                    'name': category.name,
                    'description': category.description,
                    'parent_id': str(category.parent_id) if category.parent_id else None,
                    'is_active': category.is_active
                },
                'timestamp': (category.updated_at or category.created_at).isoformat() if (category.updated_at or category.created_at) else None
            })
        
        # 3. Clients
        from app.models.customer import Customer
        query = db.query(Customer).filter(Customer.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Customer.updated_at >= since) | (Customer.created_at >= since)
            )
        
        for customer in query.all():
            changes['customers'].append({
                'id': str(customer.id),
                'action': 'update' if (customer.updated_at and since and customer.updated_at >= since) else 'create',
                'data': {
                    'id': str(customer.id),
                    'name': customer.name,
                    'email': customer.email,
                    'phone': customer.phone,
                    'address': customer.address,
                    'city': customer.city,
                    'type': getattr(customer, 'type', 'regular'),
                    'total_debt': float(getattr(customer, 'total_debt', 0)),
                    'total_purchases': float(getattr(customer, 'total_purchases', 0)),
                    'is_active': customer.is_active,
                    'branch_id': str(customer.branch_id) if customer.branch_id else None,
                    'pharmacy_id': str(customer.pharmacy_id) if customer.pharmacy_id else None
                },
                'timestamp': (customer.updated_at or customer.created_at).isoformat() if (customer.updated_at or customer.created_at) else None
            })
        
        # 4. Ventes
        from app.models.sale import Sale, SaleItem
        query = db.query(Sale).filter(Sale.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Sale.updated_at >= since) | (Sale.created_at >= since)
            )
        
        for sale in query.all():
            changes['sales'].append({
                'id': str(sale.id),
                'action': 'update' if (sale.updated_at and since and sale.updated_at >= since) else 'create',
                'data': {
                    'id': str(sale.id),
                    'reference': sale.reference,
                    'total_amount': float(sale.total_amount),
                    'customer_name': sale.customer_name,
                    'customer_phone': sale.customer_phone,
                    'customer_email': sale.customer_email,
                    'payment_method': sale.payment_method,
                    'is_credit': sale.is_credit,
                    'status': sale.status,
                    'branch_id': str(sale.branch_id) if sale.branch_id else None,
                    'pharmacy_id': str(sale.pharmacy_id) if sale.pharmacy_id else None,
                    'created_by': str(sale.created_by) if sale.created_by else None,
                    'seller_name': sale.seller_name,
                    'global_discount': float(sale.global_discount) if sale.global_discount else 0,
                    'notes': sale.notes,
                    'created_at': sale.created_at.isoformat() if sale.created_at else None,
                    'updated_at': sale.updated_at.isoformat() if sale.updated_at else None,
                    'items': [
                        {
                            'id': str(item.id),
                            'product_id': str(item.product_id),
                            'product_name': item.product_name,
                            'product_code': item.product_code,
                            'quantity': float(item.quantity),
                            'unit_price': float(item.unit_price),
                            'discount_percent': float(item.discount_percent) if item.discount_percent else 0,
                            'discount_amount': float(item.discount_amount) if item.discount_amount else 0,
                            'tva_rate': float(item.tva_rate) if item.tva_rate else 0,
                            'tva_amount': float(item.tva_amount) if item.tva_amount else 0,
                            'subtotal': float(item.subtotal) if item.subtotal else 0,
                            'total': float(item.total) if item.total else 0
                        }
                        for item in sale.items
                    ]
                },
                'timestamp': (sale.updated_at or sale.created_at).isoformat() if (sale.updated_at or sale.created_at) else None
            })
        
        # 5. Dettes
        from app.models.debt import Debt
        query = db.query(Debt).filter(Debt.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Debt.updated_at >= since) | (Debt.created_at >= since)
            )
        
        for debt in query.all():
            changes['debts'].append({
                'id': str(debt.id),
                'action': 'update' if (debt.updated_at and since and debt.updated_at >= since) else 'create',
                'data': {
                    'id': str(debt.id),
                    'customer_id': str(debt.customer_id) if debt.customer_id else None,
                    'customer_name': debt.customer.name if debt.customer else None,
                    'sale_id': str(debt.sale_id) if debt.sale_id else None,
                    'debt_number': getattr(debt, 'debt_number', f"DEBT-{debt.created_at.strftime('%Y%m%d')}" if debt.created_at else None),
                    'initial_amount': float(debt.initial_amount),
                    'total_amount': float(debt.total_amount) if hasattr(debt, 'total_amount') else float(debt.initial_amount),
                    'paid_amount': float(debt.paid_amount),
                    'remaining_amount': float(debt.remaining_amount),
                    'interest_rate': float(debt.interest_rate) if debt.interest_rate else 0,
                    'interest_amount': float(debt.interest_amount) if debt.interest_amount else 0,
                    'issue_date': debt.issue_date.isoformat() if debt.issue_date else None,
                    'due_date': debt.due_date.isoformat() if debt.due_date else None,
                    'status': debt.status,
                    'is_overdue': debt.days_overdue > 0 if hasattr(debt, 'days_overdue') else False,
                    'notes': debt.notes,
                    'is_active': debt.is_active,
                    'branch_id': str(debt.branch_id) if hasattr(debt, 'branch_id') and debt.branch_id else None,
                    'pharmacy_id': str(debt.pharmacy_id) if hasattr(debt, 'pharmacy_id') and debt.pharmacy_id else None,
                    'created_at': debt.created_at.isoformat() if debt.created_at else None,
                    'updated_at': debt.updated_at.isoformat() if debt.updated_at else None
                },
                'timestamp': (debt.updated_at or debt.created_at).isoformat() if (debt.updated_at or debt.created_at) else None
            })
        
        # 6. Paiements de dettes
        from app.models.debt_payment import DebtPayment
        query = db.query(DebtPayment).filter(DebtPayment.tenant_id == tenant_id)
        if since:
            query = query.filter(DebtPayment.created_at >= since)
        
        for payment in query.all():
            changes['debt_payments'].append({
                'id': str(payment.id),
                'action': 'create',
                'data': {
                    'id': str(payment.id),
                    'debt_id': str(payment.debt_id),
                    'customer_id': str(payment.customer_id) if payment.customer_id else None,
                    'amount': float(payment.amount),
                    'payment_method': payment.payment_method,
                    'payment_date': payment.payment_date.isoformat() if hasattr(payment, 'payment_date') and payment.payment_date else payment.created_at.isoformat(),
                    'reference': payment.reference,
                    'notes': payment.notes,
                    'processed_by': str(payment.processed_by) if payment.processed_by else None,
                    'created_at': payment.created_at.isoformat() if payment.created_at else None
                },
                'timestamp': payment.created_at.isoformat() if payment.created_at else None
            })
        
        # 7. Utilisateurs
        from app.models.user import User
        query = db.query(User).filter(User.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (User.updated_at >= since) | (User.created_at >= since)
            )
        
        for user in query.all():
            changes['users'].append({
                'id': str(user.id),
                'action': 'update' if (user.updated_at and since and user.updated_at >= since) else 'create',
                'data': {
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
                },
                'timestamp': (user.updated_at or user.created_at).isoformat() if (user.updated_at or user.created_at) else None
            })
        
        # 8. Branches
        from app.models.branch import Branch
        query = db.query(Branch).filter(Branch.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Branch.updated_at >= since) | (Branch.created_at >= since)
            )
        
        for branch in query.all():
            changes['branches'].append({
                'id': str(branch.id),
                'action': 'update' if (branch.updated_at and since and branch.updated_at >= since) else 'create',
                'data': {
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
                },
                'timestamp': (branch.updated_at or branch.created_at).isoformat() if (branch.updated_at or branch.created_at) else None
            })
        
        # 9. Pharmacies
        from app.models.pharmacy import Pharmacy
        query = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant_id)
        if since:
            query = query.filter(
                (Pharmacy.updated_at >= since) | (Pharmacy.created_at >= since)
            )
        
        for pharmacy in query.all():
            changes['pharmacies'].append({
                'id': str(pharmacy.id),
                'action': 'update' if (pharmacy.updated_at and since and pharmacy.updated_at >= since) else 'create',
                'data': {
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
                },
                'timestamp': (pharmacy.updated_at or pharmacy.created_at).isoformat() if (pharmacy.updated_at or pharmacy.created_at) else None
            })
        
        # 10. Abonnements des branches (BranchSubscription)
        from app.models.branch_subscription import BranchSubscription
        from app.models.branch import Branch

        # Récupérer toutes les branches du tenant
        branches_query = db.query(Branch.id).filter(Branch.tenant_id == tenant_id)
        branch_ids = [b[0] for b in branches_query.all()]

        if branch_ids:
            query = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id.in_(branch_ids),
                BranchSubscription.tenant_id == tenant_id
            )
            if since:
                query = query.filter(
                    (BranchSubscription.updated_at >= since) | 
                    (BranchSubscription.created_at >= since)
                )
            
            for sub in query.all():
                changes['subscriptions'].append({
                    'id': str(sub.id),
                    'action': 'update' if (sub.updated_at and since and sub.updated_at >= since) else 'create',
                    'data': {
                        'id': str(sub.id),
                        'branch_id': str(sub.branch_id),
                        'tenant_id': str(sub.tenant_id),
                        'pharmacy_id': str(sub.pharmacy_id) if sub.pharmacy_id else None,
                        'plan_name': sub.plan_name,
                        'plan_type': sub.plan,
                        'status': sub.status,
                        'max_users': sub.max_users,
                        'max_products': sub.max_products,
                        'max_storage_mb': sub.max_storage_mb,
                        'billing_cycle': sub.billing_cycle,
                        'price': sub.price,
                        'currency': sub.currency,
                        'current_period_start': sub.start_date.isoformat() if sub.start_date else None,
                        'current_period_end': sub.end_date.isoformat() if sub.end_date else None,
                        'trial_end_date': sub.trial_end_date.isoformat() if sub.trial_end_date else None,
                        'is_trial': sub.status == 'TRIAL',
                        'days_remaining': sub.days_remaining(),
                        'auto_renew': sub.auto_renew,
                        'cancel_at_period_end': not sub.auto_renew,
                        'is_active': sub.is_active(),
                        'created_at': sub.created_at.isoformat() if sub.created_at else None,
                        'updated_at': sub.updated_at.isoformat() if sub.updated_at else None
                    },
                    'timestamp': (sub.updated_at or sub.created_at).isoformat() if (sub.updated_at or sub.created_at) else None
                })
        
        # 11. Mouvements de stock
        from app.models.stock_movement import StockMovement
        query = db.query(StockMovement).filter(StockMovement.tenant_id == tenant_id)
        if since:
            query = query.filter(StockMovement.created_at >= since)
        
        for movement in query.order_by(StockMovement.created_at.desc()).limit(500).all():
            changes['stock_movements'].append({
                'id': str(movement.id),
                'action': 'create',
                'data': {
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
                },
                'timestamp': movement.created_at.isoformat() if movement.created_at else None
            })
        
        logger.info(f"Récupéré {sum(len(v) for v in changes.values())} changements pour tenant {tenant_id}")
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des changements: {str(e)}", exc_info=True)
        raise
    
    return changes


def get_sync_status(db: Session, tenant_id: UUID) -> Dict[str, Any]:
    """
    Récupère le statut de synchronisation pour un tenant.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        
    Returns:
        Dict contenant le statut de synchronisation
    """
    from app.models.sale import Sale
    from app.models.product import Product
    from app.models.user import User
    from app.models.branch import Branch
    from app.models.debt import Debt
    from app.models.customer import Customer
    
    last_sale = db.query(Sale).filter(Sale.tenant_id == tenant_id).order_by(Sale.updated_at.desc()).first()
    last_product = db.query(Product).filter(Product.tenant_id == tenant_id).order_by(Product.updated_at.desc()).first()
    last_user = db.query(User).filter(User.tenant_id == tenant_id).order_by(User.updated_at.desc()).first()
    last_branch = db.query(Branch).filter(Branch.tenant_id == tenant_id).order_by(Branch.updated_at.desc()).first()
    last_debt = db.query(Debt).filter(Debt.tenant_id == tenant_id).order_by(Debt.updated_at.desc()).first()
    last_customer = db.query(Customer).filter(Customer.tenant_id == tenant_id).order_by(Customer.updated_at.desc()).first()
    
    counts = {
        'sales': db.query(Sale).filter(Sale.tenant_id == tenant_id).count(),
        'products': db.query(Product).filter(Product.tenant_id == tenant_id).count(),
        'users': db.query(User).filter(User.tenant_id == tenant_id).count(),
        'branches': db.query(Branch).filter(Branch.tenant_id == tenant_id).count(),
        'debts': db.query(Debt).filter(Debt.tenant_id == tenant_id).count(),
        'customers': db.query(Customer).filter(Customer.tenant_id == tenant_id).count(),
    }
    
    total_pending_debts = db.query(Debt).filter(
        Debt.tenant_id == tenant_id,
        Debt.remaining_amount > 0,
        Debt.status.in_(["pending", "partial", "overdue"])
    ).count()
    
    total_overdue_amount = db.query(Debt).filter(
        Debt.tenant_id == tenant_id,
        Debt.due_date < date.today(),
        Debt.remaining_amount > 0
    ).with_entities(Debt.remaining_amount).all()
    
    total_overdue = sum(float(d[0]) for d in total_overdue_amount) if total_overdue_amount else 0
    
    return {
        'last_sync': None,
        'status': 'active',
        'tenant_id': str(tenant_id),
        'last_update': {
            'sales': last_sale.updated_at.isoformat() if last_sale else None,
            'products': last_product.updated_at.isoformat() if last_product else None,
            'users': last_user.updated_at.isoformat() if last_user else None,
            'branches': last_branch.updated_at.isoformat() if last_branch else None,
            'debts': last_debt.updated_at.isoformat() if last_debt else None,
            'customers': last_customer.updated_at.isoformat() if last_customer else None
        },
        'counts': counts,
        'debts_summary': {
            'total_pending_debts': total_pending_debts,
            'total_overdue_amount': total_overdue
        }
    }


# ==========================================================
# HANDLERS PAR TABLE
# ==========================================================

def _sync_product(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un produit"""
    from app.models.product import Product
    from uuid import UUID as UUIDType
    
    try:
        product_id = data.get('id')
        if product_id and isinstance(product_id, str):
            product_id = UUIDType(product_id)
        
        existing = None
        if product_id:
            existing = db.query(Product).filter(
                Product.id == product_id,
                Product.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                existing.deleted_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(product_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Produit non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            if hasattr(existing, 'refresh_statuses'):
                existing.refresh_statuses()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            product = Product(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Product, k)}
            )
            if hasattr(product, 'refresh_statuses'):
                product.refresh_statuses()
            db.add(product)
            db.flush()
            return {'success': True, 'id': str(product.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync product: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_category(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise une catégorie"""
    from app.models.category import Category
    from uuid import UUID as UUIDType
    
    try:
        category_id = data.get('id')
        if category_id and isinstance(category_id, str):
            category_id = UUIDType(category_id)
        
        existing = None
        if category_id:
            existing = db.query(Category).filter(
                Category.id == category_id,
                Category.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(category_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Catégorie non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            category = Category(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Category, k)}
            )
            db.add(category)
            db.flush()
            return {'success': True, 'id': str(category.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync category: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_customer(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un client"""
    from app.models.customer import Customer
    from uuid import UUID as UUIDType
    
    try:
        customer_id = data.get('id')
        if customer_id and isinstance(customer_id, str):
            customer_id = UUIDType(customer_id)
        
        existing = None
        if customer_id:
            existing = db.query(Customer).filter(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(customer_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Client non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            customer = Customer(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Customer, k)}
            )
            db.add(customer)
            db.flush()
            return {'success': True, 'id': str(customer.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync customer: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_sale(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise une vente"""
    from app.models.sale import Sale, SaleItem
    from app.models.product import Product
    from app.models.stock_movement import StockMovement
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    try:
        sale_id = data.get('id')
        if sale_id and isinstance(sale_id, str):
            sale_id = UUIDType(sale_id)
        
        existing = None
        if sale_id:
            existing = db.query(Sale).filter(
                Sale.id == sale_id,
                Sale.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.status = "cancelled"
                db.flush()
                return {'success': True, 'id': str(sale_id), 'action': 'cancelled'}
            return {'success': False, 'error': 'Vente non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            items_data = data.pop('items', [])
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            items_data = data.pop('items', [])
            data.pop('id', None)
            
            # Créer la vente
            sale = Sale(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Sale, k)}
            )
            db.add(sale)
            db.flush()
            
            # Créer les items et gérer le stock
            for item_data in items_data:
                item_data.pop('id', None)
                
                # Récupérer le produit pour la gestion du stock
                product_id = item_data.get('product_id')
                if product_id and isinstance(product_id, str):
                    product_id = UUIDType(product_id)
                
                product = None
                if product_id:
                    product = db.query(Product).filter(
                        Product.id == product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                
                quantity = Decimal(str(item_data.get('quantity', 1)))
                
                sale_item = SaleItem(
                    tenant_id=tenant_id,
                    sale_id=sale.id,
                    **{k: v for k, v in item_data.items() if hasattr(SaleItem, k)}
                )
                db.add(sale_item)
                db.flush()
                
                # Mettre à jour le stock si nécessaire
                if product and sale.status == "completed":
                    old_quantity = product.quantity or 0
                    new_quantity = max(0, old_quantity - int(quantity))
                    product.quantity = new_quantity
                    if hasattr(product, 'available_quantity'):
                        product.available_quantity = max(0, new_quantity - (product.reserved_quantity or 0))
                    if hasattr(product, 'refresh_statuses'):
                        product.refresh_statuses()
                    
                    # Mouvement de stock
                    movement = StockMovement(
                        tenant_id=tenant_id,
                        product_id=product.id,
                        pharmacy_id=sale.pharmacy_id,
                        branch_id=sale.branch_id,
                        quantity_before=old_quantity,
                        quantity_after=new_quantity,
                        quantity_change=-int(quantity),
                        movement_type="sale",
                        reason=f"Vente #{sale.reference}",
                        reference=sale.reference,
                        sale_id=sale.id,
                        sale_item_id=sale_item.id,
                        created_by=sale.created_by
                    )
                    db.add(movement)
            
            db.flush()
            return {'success': True, 'id': str(sale.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync sale: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_debt(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise une dette"""
    from app.models.debt import Debt
    from app.models.customer import Customer
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    try:
        debt_id = data.get('id')
        if debt_id and isinstance(debt_id, str):
            debt_id = UUIDType(debt_id)
        
        existing = None
        if debt_id:
            existing = db.query(Debt).filter(
                Debt.id == debt_id,
                Debt.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(debt_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Dette non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    # Gérer les conversions Decimal
                    if field in ['initial_amount', 'paid_amount', 'remaining_amount', 'interest_rate', 'interest_amount']:
                        value = Decimal(str(value)) if value else Decimal('0')
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            
            # Mettre à jour le statut automatiquement
            if hasattr(existing, 'update_status'):
                existing.update_status()
            
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            # S'assurer que le client existe
            customer_id = data.get('customer_id')
            if customer_id:
                if isinstance(customer_id, str):
                    customer_id = UUIDType(customer_id)
                
                customer = db.query(Customer).filter(
                    Customer.id == customer_id,
                    Customer.tenant_id == tenant_id
                ).first()
                
                if not customer and data.get('customer_name'):
                    # Créer le client s'il n'existe pas
                    customer = Customer(
                        tenant_id=tenant_id,
                        id=customer_id,
                        name=data.get('customer_name'),
                        phone=data.get('customer_phone'),
                        email=data.get('customer_email')
                    )
                    db.add(customer)
                    db.flush()
            
            data.pop('id', None)
            
            # Convertir les valeurs Decimal
            for field in ['initial_amount', 'paid_amount', 'remaining_amount', 'interest_rate', 'interest_amount']:
                if field in data and data[field] is not None:
                    data[field] = Decimal(str(data[field]))
            
            debt = Debt(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Debt, k)}
            )
            db.add(debt)
            db.flush()
            
            # Mettre à jour le total des dettes du client
            if debt.customer_id:
                total_debt = db.query(Debt).filter(
                    Debt.customer_id == debt.customer_id,
                    Debt.remaining_amount > 0
                ).with_entities(Debt.remaining_amount).all()
                total = sum(float(d[0]) for d in total_debt) if total_debt else 0
                
                customer = db.query(Customer).filter(Customer.id == debt.customer_id).first()
                if customer:
                    customer.total_debt = total
                    db.flush()
            
            return {'success': True, 'id': str(debt.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync debt: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_debt_payment(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un paiement de dette"""
    from app.models.debt import Debt, DebtPayment
    from app.models.customer import Customer
    from uuid import UUID as UUIDType
    from decimal import Decimal
    
    try:
        payment_id = data.get('id')
        if payment_id and isinstance(payment_id, str):
            payment_id = UUIDType(payment_id)
        
        existing = None
        if payment_id:
            existing = db.query(DebtPayment).filter(
                DebtPayment.id == payment_id,
                DebtPayment.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                # Rembourser le paiement
                debt = db.query(Debt).filter(Debt.id == existing.debt_id).first()
                if debt:
                    debt.remaining_amount += existing.amount
                    debt.paid_amount -= existing.amount
                    if debt.remaining_amount > 0:
                        debt.status = "partial" if debt.paid_amount > 0 else "pending"
                    if hasattr(debt, 'update_status'):
                        debt.update_status()
                
                db.delete(existing)
                db.flush()
                return {'success': True, 'id': str(payment_id), 'action': 'deleted'}
            return {'success': False, 'error': 'Paiement non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            return {'success': True, 'id': str(existing.id), 'action': 'ignored', 'reason': 'Debt payments are immutable'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            debt_id = data.get('debt_id')
            if debt_id and isinstance(debt_id, str):
                debt_id = UUIDType(debt_id)
            
            debt = db.query(Debt).filter(
                Debt.id == debt_id,
                Debt.tenant_id == tenant_id
            ).first()
            
            if not debt:
                return {'success': False, 'error': f'Dette {debt_id} non trouvée'}
            
            amount = Decimal(str(data.get('amount', 0)))
            
            if amount > debt.remaining_amount:
                return {'success': False, 'error': f'Le paiement ({amount}) dépasse le solde restant ({debt.remaining_amount})'}
            
            data.pop('id', None)
            data['tenant_id'] = tenant_id
            data['debt_id'] = debt.id
            
            payment = DebtPayment(
                **{k: v for k, v in data.items() if hasattr(DebtPayment, k)}
            )
            db.add(payment)
            db.flush()
            
            # Mettre à jour la dette
            debt.remaining_amount -= amount
            debt.paid_amount += amount
            
            if debt.remaining_amount <= 0:
                debt.status = "paid"
            elif debt.paid_amount > 0:
                debt.status = "partial"
            
            if hasattr(debt, 'update_status'):
                debt.update_status()
            
            db.flush()
            
            return {'success': True, 'id': str(payment.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync debt payment: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_user(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un utilisateur"""
    from app.models.user import User
    from app.core.security import hash_password
    from uuid import UUID as UUIDType
    
    try:
        user_id = data.get('id')
        if user_id and isinstance(user_id, str):
            user_id = UUIDType(user_id)
        
        existing = None
        if user_id:
            existing = db.query(User).filter(
                User.id == user_id,
                User.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.actif = False
                db.flush()
                return {'success': True, 'id': str(user_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Utilisateur non trouvé'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and field != 'password' and hasattr(existing, field):
                    setattr(existing, field, value)
            if data.get('password'):
                existing.password_hash = hash_password(data['password'])
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            password = data.pop('password', None)
            user = User(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(User, k)}
            )
            if password:
                user.password_hash = hash_password(password)
            db.add(user)
            db.flush()
            return {'success': True, 'id': str(user.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync user: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_branch(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise une branche/succursale"""
    from app.models.branch import Branch
    from uuid import UUID as UUIDType
    
    try:
        branch_id = data.get('id')
        if branch_id and isinstance(branch_id, str):
            branch_id = UUIDType(branch_id)
        
        existing = None
        if branch_id:
            existing = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(branch_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Branche non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            branch = Branch(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Branch, k)}
            )
            db.add(branch)
            db.flush()
            return {'success': True, 'id': str(branch.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync branch: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


def _sync_pharmacy(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise une pharmacie"""
    from app.models.pharmacy import Pharmacy
    from uuid import UUID as UUIDType
    
    try:
        pharmacy_id = data.get('id')
        if pharmacy_id and isinstance(pharmacy_id, str):
            pharmacy_id = UUIDType(pharmacy_id)
        
        existing = None
        if pharmacy_id:
            existing = db.query(Pharmacy).filter(
                Pharmacy.id == pharmacy_id,
                Pharmacy.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                existing.is_active = False
                db.flush()
                return {'success': True, 'id': str(pharmacy_id), 'action': 'deactivated'}
            return {'success': False, 'error': 'Pharmacie non trouvée'}
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            for field, value in data.items():
                if field != 'id' and hasattr(existing, field):
                    setattr(existing, field, value)
            existing.updated_at = datetime.utcnow()
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            pharmacy = Pharmacy(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(Pharmacy, k)}
            )
            db.add(pharmacy)
            db.flush()
            return {'success': True, 'id': str(pharmacy.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync pharmacy: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}

def _sync_subscription(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un abonnement de branche (BranchSubscription)"""
    from app.models.branch_subscription import BranchSubscription, SubscriptionPlan, SubscriptionStatus
    from app.models.branch import Branch
    from uuid import UUID as UUIDType
    from datetime import datetime, timedelta
    
    try:
        sub_id = data.get('id')
        if sub_id and isinstance(sub_id, str):
            sub_id = UUIDType(sub_id)
        
        branch_id = data.get('branch_id')
        if branch_id and isinstance(branch_id, str):
            branch_id = UUIDType(branch_id)
        
        # Vérifier que la branche existe et appartient au tenant
        branch = None
        if branch_id:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id
            ).first()
            
            if not branch:
                return {'success': False, 'error': f'Branche {branch_id} non trouvée ou n\'appartient pas au tenant'}
        
        existing = None
        if sub_id:
            existing = db.query(BranchSubscription).filter(
                BranchSubscription.id == sub_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
        elif branch_id:
            # Chercher l'abonnement de la branche
            existing = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id == branch_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        if action_upper == 'DELETE':
            if existing:
                # Soft delete: marquer comme annulé
                existing.status = SubscriptionStatus.CANCELLED.value
                existing.cancelled_at = datetime.utcnow()
                existing.updated_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(existing.id), 'action': 'cancelled'}
            return {'success': False, 'error': 'Abonnement non trouvé'}
        
        # Préparer les données
        data['branch_id'] = branch_id
        data['tenant_id'] = tenant_id
        data.pop('id', None)
        
        # Convertir les champs
        if 'plan' in data:
            plan_value = data['plan'].upper() if isinstance(data['plan'], str) else data['plan']
            data['plan'] = plan_value
        
        if 'status' in data:
            status_value = data['status'].upper() if isinstance(data['status'], str) else data['status']
            data['status'] = status_value
        
        # Gérer la période d'essai
        if data.get('is_trial') and not data.get('trial_end_date'):
            data['trial_end_date'] = datetime.utcnow() + timedelta(days=14)
            if not data.get('end_date'):
                data['end_date'] = data['trial_end_date']
        
        if existing and action_upper in ['UPDATE', 'UPSERT']:
            # Mise à jour limitée
            updatable_fields = ['status', 'auto_renew', 'billing_cycle', 'plan', 'plan_name', 
                                'max_products', 'max_users', 'max_storage_mb', 'end_date', 
                                'trial_end_date', 'price', 'currency', 'cancelled_reason']
            for field in updatable_fields:
                if field in data and hasattr(existing, field):
                    setattr(existing, field, data[field])
            existing.updated_at = datetime.utcnow()
            
            # Recalculer le statut si nécessaire
            if existing.end_date and existing.end_date < datetime.utcnow():
                existing.status = SubscriptionStatus.EXPIRED.value
            
            db.flush()
            return {'success': True, 'id': str(existing.id), 'action': 'updated'}
        
        elif action_upper in ['CREATE', 'UPSERT']:
            # Vérifier que la branche existe
            if not branch:
                return {'success': False, 'error': 'branch_id requis pour la création'}
            
            # Vérifier qu'il n'y a pas déjà un abonnement pour cette branche
            existing_for_branch = db.query(BranchSubscription).filter(
                BranchSubscription.branch_id == branch_id,
                BranchSubscription.tenant_id == tenant_id
            ).first()
            
            if existing_for_branch:
                # Mettre à jour l'existant au lieu de créer
                for field, value in data.items():
                    if hasattr(existing_for_branch, field):
                        setattr(existing_for_branch, field, value)
                existing_for_branch.updated_at = datetime.utcnow()
                db.flush()
                return {'success': True, 'id': str(existing_for_branch.id), 'action': 'updated'}
            
            # Créer un nouvel abonnement
            subscription = BranchSubscription(
                branch_id=branch_id,
                tenant_id=tenant_id,
                pharmacy_id=branch.parent_pharmacy_id if branch.parent_pharmacy_id else None,
                plan=data.get('plan', SubscriptionPlan.TRIAL.value),
                plan_name=data.get('plan_name', 'Essai'),
                start_date=data.get('start_date', datetime.utcnow()),
                end_date=data.get('end_date', datetime.utcnow() + timedelta(days=30)),
                trial_end_date=data.get('trial_end_date'),
                status=data.get('status', SubscriptionStatus.TRIAL.value if data.get('is_trial') else SubscriptionStatus.ACTIVE.value),
                billing_cycle=data.get('billing_cycle', 'monthly'),
                price=data.get('price', 0.0),
                currency=data.get('currency', 'EUR'),
                auto_renew=data.get('auto_renew', True),
                max_products=data.get('max_products', 100),
                max_users=data.get('max_users', 5),
                max_storage_mb=data.get('max_storage_mb', 100)
            )
            db.add(subscription)
            db.flush()
            
            # Lier l'abonnement à la branche
            branch.subscription_id = subscription.id
            db.flush()
            
            return {'success': True, 'id': str(subscription.id), 'action': 'created'}
        
        return {'success': False, 'error': f'Action non supportée: {action}'}
        
    except Exception as e:
        logger.error(f"Erreur sync subscription: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}
    
def _sync_stock_movement(db: Session, tenant_id: UUID, action: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Synchronise un mouvement de stock"""
    from app.models.stock_movement import StockMovement
    from uuid import UUID as UUIDType
    
    try:
        movement_id = data.get('id')
        if movement_id and isinstance(movement_id, str):
            movement_id = UUIDType(movement_id)
        
        existing = None
        if movement_id:
            existing = db.query(StockMovement).filter(
                StockMovement.id == movement_id,
                StockMovement.tenant_id == tenant_id
            ).first()
        
        action_upper = action.upper()
        
        # Les mouvements de stock sont généralement en lecture seule côté offline
        if not existing and action_upper in ['CREATE', 'UPSERT']:
            data.pop('id', None)
            movement = StockMovement(
                tenant_id=tenant_id,
                **{k: v for k, v in data.items() if hasattr(StockMovement, k)}
            )
            db.add(movement)
            db.flush()
            return {'success': True, 'id': str(movement.id), 'action': 'created'}
        
        return {'success': True, 'action': 'ignored', 'reason': 'Stock movements are read-only'}
        
    except Exception as e:
        logger.error(f"Erreur sync stock movement: {e}", exc_info=True)
        return {'success': False, 'error': str(e)}


# ==========================================================
# FONCTIONS UTILITAIRES
# ==========================================================

def _serialize_product(product) -> Dict[str, Any]:
    """Sérialise un produit en dictionnaire"""
    return {
        'id': str(product.id),
        'code': product.code,
        'name': product.name,
        'commercial_name': product.commercial_name,
        'barcode': product.barcode,
        'quantity': product.quantity or 0,
        'available_quantity': getattr(product, 'available_quantity', product.quantity or 0),
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
    }