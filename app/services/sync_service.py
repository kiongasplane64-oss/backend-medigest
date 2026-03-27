# app/services/sync_service.py - Service de synchronisation complet

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
import logging

from app.models.sync_log import SyncLog

logger = logging.getLogger(__name__)


# ==========================================================
# FONCTIONS PRINCIPALES DE SYNCHRONISATION
# ==========================================================

def process_sync(
    db: Session,
    tenant_id: str,
    items: List[Any],
) -> Dict[str, Any]:
    """
    Traite les données de synchronisation envoyées par un client mobile.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        items: Liste des items à synchroniser (objets ou dictionnaires)
        
    Returns:
        Dict contenant le statut, le nombre d'items traités et les erreurs
    """
    processed = 0
    errors = []
    
    try:
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
            
            # ---- Validation minimale ----
            if not table_name or not action:
                errors.append({
                    "item": item,
                    "error": "table_name ou action manquant",
                })
                continue
            
            # ---- Enregistrement du log de synchronisation ----
            log = SyncLog(
                tenant_id=tenant_id,
                table_name=table_name,
                action=action,
                data=data,
                created_at=datetime.utcnow(),
            )
            db.add(log)
            
            # ---- Application sur les tables métier ----
            logger.info(f"Traitement de {table_name} - {action} pour tenant {tenant_id}")
            
            if table_name == 'products':
                process_product_sync(db, tenant_id, action, data)
            elif table_name == 'categories':
                process_category_sync(db, tenant_id, action, data)
            elif table_name == 'customers':
                process_customer_sync(db, tenant_id, action, data)
            elif table_name == 'orders':
                process_order_sync(db, tenant_id, action, data)
            else:
                errors.append({
                    "item": item,
                    "error": f"Table inconnue: {table_name}",
                })
                continue
            
            processed += 1
        
        db.commit()
        logger.info(f"Synchronisation terminée pour tenant {tenant_id}: {processed} items traités")
        
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Erreur SQL lors de la synchronisation: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "processed": processed,
            "errors": errors,
        }
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la synchronisation: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "processed": processed,
            "errors": errors,
        }
    
    return {
        "status": "success",
        "processed": processed,
        "errors": errors,
        "synced_at": datetime.utcnow().isoformat(),
    }

def get_changes_since(
    db: Session,
    tenant_id: str,
    last_sync: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """
    Récupère les changements depuis la dernière synchronisation.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        last_sync: Date de dernière synchronisation
        
    Returns:
        Liste des changements
    """
    changes = []
    
    try:
        # Importer les modèles ici pour éviter les imports circulaires
        from app.models.product import Product
        from app.models.category import Category
        from app.models.customer import Customer
        from app.models.order import Order
        
        # Récupérer les produits modifiés
        query_products = db.query(Product).filter(Product.tenant_id == tenant_id)
        if last_sync:
            query_products = query_products.filter(
                (Product.updated_at > last_sync) | (Product.created_at > last_sync)
            )
        
        for product in query_products.all():
            # CORRECTION: Vérifier si last_sync existe avant comparaison
            if last_sync and product.updated_at:
                action = "update" if product.updated_at > last_sync else "create"
            elif last_sync and product.created_at:
                action = "create" if product.created_at > last_sync else "update"
            else:
                # Si pas de last_sync, considérer comme création
                action = "create"
            
            changes.append({
                "table": "products",
                "action": action,
                "data": product.to_dict() if hasattr(product, 'to_dict') else {
                    "id": str(product.id),
                    "name": product.name,
                    "price": product.price,
                    "stock": getattr(product, 'stock', 0),
                },
                "timestamp": (product.updated_at or product.created_at).isoformat() if (product.updated_at or product.created_at) else None
            })
        
        # Récupérer les catégories modifiées
        query_categories = db.query(Category).filter(Category.tenant_id == tenant_id)
        if last_sync:
            query_categories = query_categories.filter(
                (Category.updated_at > last_sync) | (Category.created_at > last_sync)
            )
        
        for category in query_categories.all():
            # CORRECTION: Vérifier si last_sync existe avant comparaison
            if last_sync and category.updated_at:
                action = "update" if category.updated_at > last_sync else "create"
            elif last_sync and category.created_at:
                action = "create" if category.created_at > last_sync else "update"
            else:
                action = "create"
            
            changes.append({
                "table": "categories",
                "action": action,
                "data": {
                    "id": str(category.id),
                    "name": category.name,
                    "description": getattr(category, 'description', ''),
                },
                "timestamp": (category.updated_at or category.created_at).isoformat() if (category.updated_at or category.created_at) else None
            })
        
        # Récupérer les clients modifiés
        query_customers = db.query(Customer).filter(Customer.tenant_id == tenant_id)
        if last_sync:
            query_customers = query_customers.filter(
                (Customer.updated_at > last_sync) | (Customer.created_at > last_sync)
            )
        
        for customer in query_customers.all():
            # CORRECTION: Vérifier si last_sync existe avant comparaison
            if last_sync and customer.updated_at:
                action = "update" if customer.updated_at > last_sync else "create"
            elif last_sync and customer.created_at:
                action = "create" if customer.created_at > last_sync else "update"
            else:
                action = "create"
            
            changes.append({
                "table": "customers",
                "action": action,
                "data": {
                    "id": str(customer.id),
                    "name": customer.name,
                    "email": getattr(customer, 'email', ''),
                    "phone": getattr(customer, 'phone', ''),
                    "address": getattr(customer, 'address', ''),
                },
                "timestamp": (customer.updated_at or customer.created_at).isoformat() if (customer.updated_at or customer.created_at) else None
            })
        
        # Récupérer les commandes modifiées
        query_orders = db.query(Order).filter(Order.tenant_id == tenant_id)
        if last_sync:
            query_orders = query_orders.filter(
                (Order.updated_at > last_sync) | (Order.created_at > last_sync)
            )
        
        for order in query_orders.all():
            # CORRECTION: Vérifier si last_sync existe avant comparaison
            if last_sync and order.updated_at:
                action = "update" if order.updated_at > last_sync else "create"
            elif last_sync and order.created_at:
                action = "create" if order.created_at > last_sync else "update"
            else:
                action = "create"
            
            changes.append({
                "table": "orders",
                "action": action,
                "data": {
                    "id": str(order.id),
                    "total": getattr(order, 'total', 0),
                    "status": getattr(order, 'status', 'pending'),
                },
                "timestamp": (order.updated_at or order.created_at).isoformat() if (order.updated_at or order.created_at) else None
            })
        
        logger.info(f"Récupéré {len(changes)} changements pour tenant {tenant_id}")
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des changements: {str(e)}")
        raise
    
    return changes

def get_sync_status(db: Session, tenant_id: str) -> Dict[str, Any]:
    """
    Récupère le statut de synchronisation.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        
    Returns:
        Dict contenant le statut de synchronisation
    """
    try:
        # Récupérer la dernière synchronisation
        last_sync = db.query(SyncLog).filter(
            SyncLog.tenant_id == tenant_id
        ).order_by(SyncLog.created_at.desc()).first()
        
        # Compter les changements en attente
        pending_changes = get_pending_changes_count(db, tenant_id, last_sync.created_at if last_sync else None)
        
        return {
            "last_sync": last_sync.created_at.isoformat() if last_sync else None,
            "status": "active",
            "pending_changes": pending_changes,
            "tenant_id": tenant_id,
            "last_sync_count": get_last_sync_count(db, tenant_id),
        }
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
        return {
            "last_sync": None,
            "status": "unknown",
            "error": str(e),
            "tenant_id": tenant_id,
            "pending_changes": 0,
        }


def get_pending_changes_count(db: Session, tenant_id: str, last_sync: Optional[datetime] = None) -> int:
    """
    Compte le nombre de changements en attente de synchronisation.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        last_sync: Date de dernière synchronisation
        
    Returns:
        Nombre de changements en attente
    """
    try:
        from app.models.product import Product
        from app.models.category import Category
        from app.models.customer import Customer
        from app.models.order import Order
        
        count = 0
        
        if not last_sync:
            # Si jamais synchronisé, compter tous les enregistrements
            count += db.query(Product).filter(Product.tenant_id == tenant_id).count()
            count += db.query(Category).filter(Category.tenant_id == tenant_id).count()
            count += db.query(Customer).filter(Customer.tenant_id == tenant_id).count()
            count += db.query(Order).filter(Order.tenant_id == tenant_id).count()
        else:
            # Compter uniquement les modifications depuis last_sync
            count += db.query(Product).filter(
                Product.tenant_id == tenant_id,
                (Product.updated_at > last_sync) | (Product.created_at > last_sync)
            ).count()
            count += db.query(Category).filter(
                Category.tenant_id == tenant_id,
                (Category.updated_at > last_sync) | (Category.created_at > last_sync)
            ).count()
            count += db.query(Customer).filter(
                Customer.tenant_id == tenant_id,
                (Customer.updated_at > last_sync) | (Customer.created_at > last_sync)
            ).count()
            count += db.query(Order).filter(
                Order.tenant_id == tenant_id,
                (Order.updated_at > last_sync) | (Order.created_at > last_sync)
            ).count()
        
        return count
    except Exception as e:
        logger.error(f"Erreur lors du comptage des changements: {str(e)}")
        return 0


def get_last_sync_count(db: Session, tenant_id: str) -> int:
    """
    Récupère le nombre d'items synchronisés lors de la dernière sync.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        
    Returns:
        Nombre d'items synchronisés
    """
    try:
        last_sync = db.query(SyncLog).filter(
            SyncLog.tenant_id == tenant_id
        ).order_by(SyncLog.created_at.desc()).first()
        
        if last_sync:
            # Compter les logs de la même date
            return db.query(SyncLog).filter(
                SyncLog.tenant_id == tenant_id,
                SyncLog.created_at >= last_sync.created_at.replace(hour=0, minute=0, second=0),
                SyncLog.created_at <= last_sync.created_at.replace(hour=23, minute=59, second=59)
            ).count()
        
        return 0
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du dernier comptage: {str(e)}")
        return 0


# ==========================================================
# HANDLERS PAR TABLE
# ==========================================================

def process_product_sync(db: Session, tenant_id: str, action: str, data: Dict[str, Any]):
    """
    Traite la synchronisation des produits.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        action: CREATE, UPDATE, DELETE
        data: Données du produit
    """
    from app.models.product import Product
    
    action_upper = action.upper()
    
    if action_upper == 'CREATE':
        product = Product(
            tenant_id=tenant_id,
            name=data.get('name'),
            price=data.get('price'),
            stock=data.get('stock', 0),
            description=data.get('description', ''),
            # ... autres champs
        )
        db.add(product)
        
    elif action_upper == 'UPDATE':
        product_id = data.get('id')
        if not product_id:
            logger.warning(f"ID manquant pour la mise à jour du produit")
            return
            
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == tenant_id
        ).first()
        
        if product:
            for key, value in data.items():
                if hasattr(product, key) and key not in ['id', 'tenant_id', 'created_at']:
                    setattr(product, key, value)
            product.updated_at = datetime.utcnow()
        else:
            logger.warning(f"Produit {product_id} non trouvé pour la mise à jour")
                    
    elif action_upper == 'DELETE':
        product_id = data.get('id')
        if product_id:
            db.query(Product).filter(
                Product.id == product_id,
                Product.tenant_id == tenant_id
            ).delete()
        else:
            logger.warning(f"ID manquant pour la suppression du produit")


def process_category_sync(db: Session, tenant_id: str, action: str, data: Dict[str, Any]):
    """
    Traite la synchronisation des catégories.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        action: CREATE, UPDATE, DELETE
        data: Données de la catégorie
    """
    from app.models.category import Category
    
    action_upper = action.upper()
    
    if action_upper == 'CREATE':
        category = Category(
            tenant_id=tenant_id,
            name=data.get('name'),
            description=data.get('description', '')
        )
        db.add(category)
        
    elif action_upper == 'UPDATE':
        category_id = data.get('id')
        if not category_id:
            logger.warning(f"ID manquant pour la mise à jour de la catégorie")
            return
            
        category = db.query(Category).filter(
            Category.id == category_id,
            Category.tenant_id == tenant_id
        ).first()
        
        if category:
            category.name = data.get('name', category.name)
            category.description = data.get('description', category.description)
            category.updated_at = datetime.utcnow()
        else:
            logger.warning(f"Catégorie {category_id} non trouvée pour la mise à jour")
            
    elif action_upper == 'DELETE':
        category_id = data.get('id')
        if category_id:
            db.query(Category).filter(
                Category.id == category_id,
                Category.tenant_id == tenant_id
            ).delete()
        else:
            logger.warning(f"ID manquant pour la suppression de la catégorie")


def process_customer_sync(db: Session, tenant_id: str, action: str, data: Dict[str, Any]):
    """
    Traite la synchronisation des clients.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        action: CREATE, UPDATE, DELETE
        data: Données du client
    """
    from app.models.customer import Customer
    
    action_upper = action.upper()
    
    if action_upper == 'CREATE':
        customer = Customer(
            tenant_id=tenant_id,
            name=data.get('name'),
            email=data.get('email'),
            phone=data.get('phone'),
            address=data.get('address', '')
        )
        db.add(customer)
        
    elif action_upper == 'UPDATE':
        customer_id = data.get('id')
        if not customer_id:
            logger.warning(f"ID manquant pour la mise à jour du client")
            return
            
        customer = db.query(Customer).filter(
            Customer.id == customer_id,
            Customer.tenant_id == tenant_id
        ).first()
        
        if customer:
            for key, value in data.items():
                if hasattr(customer, key) and key not in ['id', 'tenant_id', 'created_at']:
                    setattr(customer, key, value)
            customer.updated_at = datetime.utcnow()
        else:
            logger.warning(f"Client {customer_id} non trouvé pour la mise à jour")
                    
    elif action_upper == 'DELETE':
        customer_id = data.get('id')
        if customer_id:
            db.query(Customer).filter(
                Customer.id == customer_id,
                Customer.tenant_id == tenant_id
            ).delete()
        else:
            logger.warning(f"ID manquant pour la suppression du client")


def process_order_sync(db: Session, tenant_id: str, action: str, data: Dict[str, Any]):
    """
    Traite la synchronisation des commandes.
    
    Args:
        db: Session SQLAlchemy
        tenant_id: Identifiant du tenant
        action: CREATE, UPDATE, DELETE
        data: Données de la commande
    """
    from app.models.order import Order
    
    action_upper = action.upper()
    
    if action_upper == 'CREATE':
        order = Order(
            tenant_id=tenant_id,
            customer_id=data.get('customer_id'),
            total=data.get('total', 0),
            status=data.get('status', 'pending'),
            items=data.get('items', [])
        )
        db.add(order)
        
    elif action_upper == 'UPDATE':
        order_id = data.get('id')
        if not order_id:
            logger.warning(f"ID manquant pour la mise à jour de la commande")
            return
            
        order = db.query(Order).filter(
            Order.id == order_id,
            Order.tenant_id == tenant_id
        ).first()
        
        if order:
            for key, value in data.items():
                if hasattr(order, key) and key not in ['id', 'tenant_id', 'created_at']:
                    setattr(order, key, value)
            order.updated_at = datetime.utcnow()
        else:
            logger.warning(f"Commande {order_id} non trouvée pour la mise à jour")
                    
    elif action_upper == 'DELETE':
        order_id = data.get('id')
        if order_id:
            db.query(Order).filter(
                Order.id == order_id,
                Order.tenant_id == tenant_id
            ).delete()
        else:
            logger.warning(f"ID manquant pour la suppression de la commande")