# app/api/v1/endpoints/dashboard.py
"""
Tableau de bord principal avec gestion des permissions et statistiques avancées
Version complète 2026
"""

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, and_, extract, desc, or_
from datetime import datetime, timedelta, date
from typing import Optional, List, Dict, Any
import secrets
import uuid
import logging

from app.db.session import get_db
from app.models.user import User
from app.models.sale import Sale, SaleItem
from app.models.cost import Cost
from app.models.product import Product, ProductStock
from app.models.transfert import ProductTransfer, TransferStatus
from app.models.tenant import Tenant
from app.models.inventory_alert import InventoryAlert
from app.models.pharmacy import Pharmacy
from app.models.user_session import UserSession
from app.core.security import get_current_user, require_permission, has_permission
from app.api.deps import get_current_tenant, get_current_pharmacy_entity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


# ===================================================================
# FONCTIONS UTILITAIRES
# ===================================================================

def _to_float(value: Any, default: float = 0.0) -> float:
    """Convertit une valeur en float"""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _format_currency(value: float) -> str:
    """Formate un montant en devise"""
    return f"{value:,.2f} FC"


def _get_tenant_id(current_user: User, pharmacy_id: Optional[int] = None) -> int:
    """Détermine le tenant_id à utiliser"""
    if pharmacy_id and current_user.role in ['admin', 'super_admin']:
        return pharmacy_id
    return current_user.tenant_id


# ===================================================================
# ENDPOINTS PRINCIPAUX
# ===================================================================

@router.get("/stats")
@require_permission("dashboard:read")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    pharmacy_id: Optional[int] = Query(None, description="ID de la pharmacie pour les admins")
):
    """
    Retourne les statistiques complètes pour le dashboard
    Permission requise: dashboard:read
    """
    tenant_id = _get_tenant_id(current_user, pharmacy_id) if pharmacy_id else current_tenant.id if current_tenant else current_user.tenant_id
    pharmacy_id_effective = current_pharmacy.id if current_pharmacy else None
    
    # Date du jour
    today = date.today()
    first_day_month = today.replace(day=1)
    yesterday = today - timedelta(days=1)
    last_month = today - timedelta(days=30)
    
    # === VENTES ===
    # Ventes du jour
    daily_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        func.date(Sale.created_at) == today,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Ventes d'hier
    yesterday_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        func.date(Sale.created_at) == yesterday,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Ventes du mois
    monthly_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= first_day_month,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Ventes des 30 derniers jours
    last_30_days_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= last_month,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Tendance (pourcentage)
    sales_trend = 0
    if yesterday_sales > 0:
        sales_trend = ((daily_sales - yesterday_sales) / yesterday_sales) * 100
    elif daily_sales > 0:
        sales_trend = 100
    
    # Nombre de ventes
    sales_count = db.query(func.count(Sale.id)).filter(
        Sale.tenant_id == tenant_id,
        func.date(Sale.created_at) == today,
        Sale.status == "completed"
    ).scalar() or 0
    
    # === PRODUITS ===
    # Total produits
    product_query = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    if pharmacy_id_effective:
        product_query = product_query.filter(Product.pharmacy_id == pharmacy_id_effective)
    total_products = product_query.count()
    
    # Produits en rupture (quantité = 0)
    out_of_stock = product_query.filter(Product.quantity == 0).count()
    
    # Produits en stock bas (quantité <= seuil d'alerte)
    low_stock = product_query.filter(
        Product.quantity > 0,
        Product.quantity <= Product.alert_threshold
    ).count()
    
    # Produits expirés
    expired = product_query.filter(
        Product.expiry_date < today,
        Product.quantity > 0
    ).count()
    
    # Produits expirant bientôt (30 jours)
    expiring_soon = product_query.filter(
        Product.expiry_date >= today,
        Product.expiry_date <= today + timedelta(days=30),
        Product.quantity > 0
    ).count()
    
    # === VALEURS FINANCIÈRES ===
    # Valeur totale du stock (prix de vente)
    stock_value = db.query(func.sum(Product.selling_price * Product.quantity)).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    if pharmacy_id_effective:
        stock_value = stock_value.filter(Product.pharmacy_id == pharmacy_id_effective)
    total_stock_value = stock_value.scalar() or 0
    
    # Valeur d'achat totale
    purchase_value = db.query(func.sum(Product.purchase_price * Product.quantity)).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    if pharmacy_id_effective:
        purchase_value = purchase_value.filter(Product.pharmacy_id == pharmacy_id_effective)
    total_purchase_value = purchase_value.scalar() or 0
    
    # Bénéfice potentiel
    potential_profit = total_stock_value - total_purchase_value
    
    # Coûts du mois
    monthly_costs = db.query(func.sum(Cost.amount)).filter(
        Cost.tenant_id == tenant_id,
        Cost.created_at >= first_day_month
    ).scalar() or 0
    
    # Bénéfice net
    net_profit = monthly_sales - monthly_costs
    
    # Marge bénéficiaire
    profit_margin = (net_profit / monthly_sales * 100) if monthly_sales > 0 else 0
    
    # === UTILISATEURS ===
    active_users = db.query(func.count(User.id)).filter(
        User.tenant_id == tenant_id,
        User.actif == True
    ).scalar() or 0
    
    # === CLIENTS ===
    total_customers = db.query(func.count(func.distinct(Sale.client_id))).filter(
        Sale.tenant_id == tenant_id,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Panier moyen
    average_basket = monthly_sales / total_customers if total_customers > 0 else 0
    
    # === TRANSFERTS EN ATTENTE ===
    pending_transfers = db.query(func.count(ProductTransfer.id)).filter(
        ProductTransfer.tenant_id == tenant_id,
        ProductTransfer.status == TransferStatus.PENDING
    ).scalar() or 0
    
    if pharmacy_id_effective:
        pending_transfers = db.query(func.count(ProductTransfer.id)).filter(
            ProductTransfer.tenant_id == tenant_id,
            ProductTransfer.status == TransferStatus.PENDING,
            or_(
                ProductTransfer.source_pharmacy_id == pharmacy_id_effective,
                ProductTransfer.destination_pharmacy_id == pharmacy_id_effective
            )
        ).scalar() or 0
    
    # === TENANT (ABONNEMENT) ===
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    # === ALERTES ===
    alerts = db.query(InventoryAlert).filter(
        InventoryAlert.tenant_id == tenant_id,
        InventoryAlert.is_resolved == False
    ).order_by(InventoryAlert.created_at.desc()).limit(10).all()
    
    alert_list = []
    for alert in alerts:
        product = db.query(Product).filter(Product.id == alert.product_id).first()
        alert_list.append({
            "id": str(alert.id),
            "type": alert.alert_type,
            "severity": alert.severity,
            "product_name": product.name if product else "Produit inconnu",
            "product_id": str(alert.product_id) if alert.product_id else None,
            "current_stock": product.quantity if product else 0,
            "threshold": product.alert_threshold if product else 0,
            "message": alert.message,
            "expiry_date": product.expiry_date.isoformat() if product and product.expiry_date else None,
            "created_at": alert.created_at.isoformat(),
            "is_resolved": alert.is_resolved
        })
    
    # === HISTORIQUE DES VENTES (30 derniers jours) ===
    sales_history = []
    for i in range(30):
        day = today - timedelta(days=i)
        day_result = db.query(
            func.count(Sale.id).label("count"),
            func.sum(Sale.total_price).label("amount")
        ).filter(
            Sale.tenant_id == tenant_id,
            func.date(Sale.created_at) == day,
            Sale.status == "completed"
        ).first()
        
        sales_history.append({
            "date": day.isoformat(),
            "count": day_result[0] or 0,
            "amount": float(day_result[1] or 0)
        })
    
    # Rotation du stock (estimation)
    average_stock = total_stock_value / total_products if total_products > 0 else 0
    stock_turnover = (last_30_days_sales / average_stock) if average_stock > 0 else 0
    
    return {
        # Ventes
        "daily_sales": float(daily_sales),
        "daily_sales_count": sales_count,
        "monthly_sales": float(monthly_sales),
        "sales_trend": round(sales_trend, 2),
        "sales_history": sales_history,
        
        # Stock
        "total_products": total_products,
        "out_of_stock_count": out_of_stock,
        "low_stock_count": low_stock,
        "expired_count": expired,
        "expiring_soon_count": expiring_soon,
        
        # Finances
        "total_stock_value": float(total_stock_value),
        "total_purchase_value": float(total_purchase_value),
        "potential_profit": float(potential_profit),
        "monthly_costs": float(monthly_costs),
        "net_profit": float(net_profit),
        "profit_margin": round(profit_margin, 2),
        "stock_turnover": round(stock_turnover, 2),
        
        # Clients
        "total_customers": total_customers,
        "average_basket": float(average_basket),
        
        # Utilisateurs
        "active_users": active_users,
        
        # Transferts
        "pending_transfers_count": pending_transfers,
        
        # Tenant
        "tenant": {
            "id": str(tenant.id),
            "name": tenant.name,
            "plan_name": tenant.plan_name,
            "max_users": tenant.max_users,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None
        } if tenant else None,
        
        # Alertes
        "alerts": alert_list,
        "has_critical_alerts": any(a["severity"] == "high" for a in alert_list)
    }


@router.get("/alerts")
@require_permission("dashboard:read")
def get_inventory_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    limit: int = Query(10, ge=1, le=100),
    severity: Optional[str] = Query(None, description="high, medium, low")
):
    """
    Récupère les alertes d'inventaire
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    query = db.query(InventoryAlert).filter(
        InventoryAlert.tenant_id == tenant_id,
        InventoryAlert.is_resolved == False
    )
    
    if severity:
        query = query.filter(InventoryAlert.severity == severity)
    
    alerts = query.order_by(
        desc(InventoryAlert.severity_priority),
        InventoryAlert.created_at.desc()
    ).limit(limit).all()
    
    result = []
    for alert in alerts:
        product = db.query(Product).filter(Product.id == alert.product_id).first()
        result.append({
            "id": str(alert.id),
            "type": alert.alert_type,
            "severity": alert.severity,
            "severity_priority": alert.severity_priority,
            "message": alert.message,
            "product_id": str(alert.product_id),
            "product_name": product.name if product else None,
            "product_code": product.code if product else None,
            "current_stock": product.quantity if product else 0,
            "threshold": product.alert_threshold if product else 0,
            "expiry_date": product.expiry_date.isoformat() if product and product.expiry_date else None,
            "created_at": alert.created_at.isoformat(),
            "is_resolved": alert.is_resolved
        })
    
    return {
        "alerts": result,
        "total": len(result),
        "has_critical": any(a["severity"] == "high" for a in result)
    }


@router.post("/alerts/{alert_id}/resolve")
@require_permission("inventory:update")
def resolve_inventory_alert(
    alert_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """
    Marque une alerte comme résolue
    Permission requise: inventory:update
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    alert = db.query(InventoryAlert).filter(
        InventoryAlert.id == alert_id,
        InventoryAlert.tenant_id == tenant_id
    ).first()
    
    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alerte non trouvée"
        )
    
    alert.is_resolved = True
    alert.resolved_at = datetime.utcnow()
    alert.resolved_by = current_user.id
    
    db.commit()
    
    return {"message": "Alerte résolue", "alert_id": str(alert_id)}


@router.get("/sales/trends")
@require_permission("dashboard:read")
def get_sales_trends(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    period: str = Query("week", regex="^(day|week|month|year)$")
):
    """
    Retourne les tendances des ventes
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    today = date.today()
    
    if period == "day":
        # Ventes par heure aujourd'hui
        start_date = datetime.combine(today, datetime.min.time())
        group_by = extract('hour', Sale.created_at)
        label_format = "{hour}h"
    elif period == "week":
        # Ventes par jour cette semaine
        start_date = today - timedelta(days=today.weekday())
        group_by = func.date(Sale.created_at)
        label_format = "%Y-%m-%d"
    elif period == "month":
        # Ventes par jour ce mois
        start_date = today.replace(day=1)
        group_by = func.date(Sale.created_at)
        label_format = "%Y-%m-%d"
    else:  # year
        # Ventes par mois cette année
        start_date = today.replace(month=1, day=1)
        group_by = extract('month', Sale.created_at)
        label_format = "{month}"
    
    results = db.query(
        group_by.label('period'),
        func.count(Sale.id).label('count'),
        func.sum(Sale.total_price).label('amount')
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= start_date,
        Sale.status == "completed"
    ).group_by(group_by).order_by(group_by).all()
    
    return [{
        "period": str(r.period),
        "count": r.count,
        "amount": float(r.amount) if r.amount else 0
    } for r in results]


@router.get("/products/categories")
@require_permission("dashboard:read")
def get_products_by_category(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity)
):
    """
    Distribution des produits par catégorie
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    query = db.query(
        Product.category,
        func.count(Product.id).label('count'),
        func.sum(Product.quantity).label('total_quantity'),
        func.sum(Product.selling_price * Product.quantity).label('total_value')
    ).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    
    if current_pharmacy:
        query = query.filter(Product.pharmacy_id == current_pharmacy.id)
    
    results = query.group_by(Product.category).order_by(desc("total_value")).all()
    
    return [{
        "category": r.category or "Non catégorisé",
        "count": r.count,
        "total_quantity": r.total_quantity or 0,
        "total_value": float(r.total_value) if r.total_value else 0
    } for r in results]


@router.get("/expired-products")
@require_permission("dashboard:read")
def get_expired_products(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    days: int = Query(30, ge=1, le=365)
):
    """
    Récupère les produits expirés et ceux qui expirent bientôt
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    today = date.today()
    
    query = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    
    if current_pharmacy:
        query = query.filter(Product.pharmacy_id == current_pharmacy.id)
    
    # Produits expirés
    expired_products = query.filter(
        Product.expiry_date < today,
        Product.quantity > 0
    ).order_by(Product.expiry_date).all()
    
    # Produits expirant bientôt
    expiring_soon_products = query.filter(
        Product.expiry_date >= today,
        Product.expiry_date <= today + timedelta(days=days),
        Product.quantity > 0
    ).order_by(Product.expiry_date).all()
    
    # Produits en rupture
    out_of_stock_products = query.filter(
        Product.quantity == 0
    ).order_by(Product.name).all()
    
    return {
        "expired": [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "expiry_date": p.expiry_date.isoformat(),
                "quantity": p.quantity,
                "unit": p.unit,
                "selling_price": float(p.selling_price),
                "purchase_price": float(p.purchase_price)
            }
            for p in expired_products
        ],
        "expiring_soon": [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "expiry_date": p.expiry_date.isoformat(),
                "quantity": p.quantity,
                "unit": p.unit,
                "selling_price": float(p.selling_price),
                "days_left": (p.expiry_date - today).days
            }
            for p in expiring_soon_products
        ],
        "out_of_stock": [
            {
                "id": str(p.id),
                "name": p.name,
                "code": p.code,
                "quantity": p.quantity,
                "unit": p.unit,
                "threshold": p.alert_threshold
            }
            for p in out_of_stock_products
        ],
        "summary": {
            "expired_count": len(expired_products),
            "expiring_soon_count": len(expiring_soon_products),
            "out_of_stock_count": len(out_of_stock_products),
            "total_affected": len(expired_products) + len(expiring_soon_products) + len(out_of_stock_products)
        }
    }


@router.get("/products/never-sold")
@require_permission("dashboard:read")
def get_never_sold_products(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    limit: int = Query(50, ge=1, le=500)
):
    """
    Récupère les produits qui n'ont jamais été vendus
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    query = db.query(
        Product.id,
        Product.name,
        Product.code,
        Product.quantity,
        Product.purchase_price,
        Product.selling_price,
        Product.created_at,
        Product.category,
        Product.unit
    ).outerjoin(
        SaleItem, SaleItem.product_id == Product.id
    ).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True,
        SaleItem.id == None
    )
    
    if current_pharmacy:
        query = query.filter(Product.pharmacy_id == current_pharmacy.id)
    
    products = query.order_by(
        desc(Product.created_at)
    ).limit(limit).all()
    
    results = []
    total_value = 0
    
    for p in products:
        stock_value = p.quantity * p.purchase_price
        total_value += stock_value
        results.append({
            "id": str(p.id),
            "name": p.name,
            "code": p.code,
            "quantity": p.quantity,
            "category": p.category or "Non catégorisé",
            "unit": p.unit,
            "purchase_price": float(p.purchase_price),
            "selling_price": float(p.selling_price),
            "stock_value": float(stock_value),
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "days_in_stock": (date.today() - p.created_at.date()).days if p.created_at else 0
        })
    
    return {
        "products": results,
        "total_count": len(results),
        "total_value": float(total_value)
    }


@router.get("/sales/by-user")
@require_permission("dashboard:read")
def get_sales_by_user(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Récupère les ventes par utilisateur (vendeur/caissier)
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    # Date par défaut: 30 derniers jours
    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)
    
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())
    
    # Ventes par utilisateur
    query = db.query(
        Sale.created_by,
        User.nom_complet.label("user_name"),
        User.email.label("user_email"),
        User.role.label("user_role"),
        func.count(Sale.id).label("sales_count"),
        func.sum(Sale.total_price).label("total_amount"),
        func.avg(Sale.total_price).label("average_basket"),
        func.sum(SaleItem.quantity).label("items_sold")
    ).join(
        User, User.id == Sale.created_by
    ).join(
        SaleItem, SaleItem.sale_id == Sale.id
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.status == "completed",
        Sale.created_at >= start_datetime,
        Sale.created_at <= end_datetime
    )
    
    if current_pharmacy:
        query = query.filter(Sale.pharmacy_id == current_pharmacy.id)
    
    results = query.group_by(
        Sale.created_by, User.nom_complet, User.email, User.role
    ).order_by(
        desc("total_amount")
    ).all()
    
    total_amount = sum(r.total_amount or 0 for r in results)
    
    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": (end_date - start_date).days
        },
        "users": [
            {
                "user_id": str(r.created_by),
                "user_name": r.user_name,
                "user_email": r.user_email,
                "user_role": r.user_role,
                "sales_count": r.sales_count,
                "total_amount": float(r.total_amount),
                "average_basket": float(r.average_basket or 0),
                "items_sold": int(r.items_sold or 0),
                "percentage": (float(r.total_amount) / total_amount * 100) if total_amount > 0 else 0
            }
            for r in results
        ],
        "summary": {
            "total_users": len(results),
            "total_sales_count": sum(r.sales_count for r in results),
            "total_amount": float(total_amount),
            "average_per_user": float(total_amount / len(results)) if results else 0,
            "total_items_sold": sum(r.items_sold or 0 for r in results)
        }
    }


@router.get("/daily-profit")
@require_permission("dashboard:read")
def get_daily_profit(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    target_date: Optional[date] = Query(None)
):
    """
    Récupère le bénéfice journalier détaillé
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    if not target_date:
        target_date = date.today()
    
    start_datetime = datetime.combine(target_date, datetime.min.time())
    end_datetime = datetime.combine(target_date, datetime.max.time())
    
    # Ventes du jour avec détails des coûts
    sales_query = db.query(
        Sale.id,
        Sale.reference,
        Sale.total_price,
        Sale.payment_method,
        Sale.created_at,
        Sale.created_by
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.status == "completed",
        Sale.created_at >= start_datetime,
        Sale.created_at <= end_datetime
    )
    
    if current_pharmacy:
        sales_query = sales_query.filter(Sale.pharmacy_id == current_pharmacy.id)
    
    sales_data = sales_query.all()
    
    # Calcul du coût d'achat des produits vendus
    total_cost = 0
    sale_details = []
    
    for sale in sales_data:
        # Récupérer les items de vente avec les prix d'achat
        items = db.query(
            SaleItem.quantity,
            SaleItem.unit_price,
            SaleItem.total,
            Product.purchase_price
        ).join(
            Product, Product.id == SaleItem.product_id
        ).filter(
            SaleItem.sale_id == sale.id
        ).all()
        
        sale_cost = sum(item.quantity * (item.purchase_price or 0) for item in items)
        sale_profit = sale.total_price - sale_cost
        
        total_cost += sale_cost
        
        sale_details.append({
            "sale_id": str(sale.id),
            "reference": sale.reference,
            "total_amount": float(sale.total_price),
            "cost_amount": float(sale_cost),
            "profit": float(sale_profit),
            "profit_margin": (sale_profit / sale.total_price * 100) if sale.total_price > 0 else 0,
            "payment_method": sale.payment_method,
            "created_at": sale.created_at.isoformat() if sale.created_at else None
        })
    
    total_sales = sum(s.total_price for s in sales_data)
    total_profit = total_sales - total_cost
    
    # Coûts opérationnels du jour
    operational_costs = db.query(func.sum(Cost.amount)).filter(
        Cost.tenant_id == tenant_id,
        Cost.created_at >= start_datetime,
        Cost.created_at <= end_datetime,
        Cost.type == "operational"
    ).scalar() or 0
    
    net_profit = total_profit - operational_costs
    
    return {
        "date": target_date.isoformat(),
        "summary": {
            "total_sales": float(total_sales),
            "total_cost": float(total_cost),
            "gross_profit": float(total_profit),
            "operational_costs": float(operational_costs),
            "net_profit": float(net_profit),
            "profit_margin": round((total_profit / total_sales * 100), 2) if total_sales > 0 else 0,
            "sales_count": len(sales_data)
        },
        "sales": sale_details
    }


# ===================================================================
# SESSIONS UTILISATEUR
# ===================================================================

@router.post("/session/register")
@require_permission("dashboard:write")
def register_user_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    platform: str = Query("web", description="Plateforme: web, mobile, pos, tablet"),
    device_type: Optional[str] = Query(None),
    device_name: Optional[str] = Query(None),
    browser: Optional[str] = Query(None),
    browser_version: Optional[str] = Query(None),
    os: Optional[str] = Query(None),
    os_version: Optional[str] = Query(None),
    ip_address: Optional[str] = Query(None),
    user_agent: Optional[str] = Query(None),
    location_city: Optional[str] = Query(None),
    location_country: Optional[str] = Query(None)
):
    """
    Enregistre une nouvelle session utilisateur (multi-plateforme)
    Permission requise: dashboard:write
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    # Générer un ID de session unique
    session_id = f"{current_user.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    
    # Expiration dans 30 jours
    expires_at = datetime.utcnow() + timedelta(days=30)
    
    # Créer la session
    session = UserSession(
        id=uuid.uuid4(),
        user_id=current_user.id,
        tenant_id=tenant_id,
        session_id=session_id,
        platform=platform,
        device_type=device_type,
        device_name=device_name,
        browser=browser,
        browser_version=browser_version,
        os=os,
        os_version=os_version,
        ip_address=ip_address,
        user_agent=user_agent,
        location_city=location_city,
        location_country=location_country,
        expires_at=expires_at
    )
    
    db.add(session)
    db.commit()
    db.refresh(session)
    
    return {
        "session_id": session_id,
        "platform": session.platform,
        "device_name": session.device_name,
        "created_at": session.created_at.isoformat(),
        "expires_at": session.expires_at.isoformat()
    }


@router.get("/sessions")
@require_permission("dashboard:read")
def get_user_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    include_inactive: bool = Query(False)
):
    """
    Récupère toutes les sessions actives de l'utilisateur sur toutes les plateformes
    Permission requise: dashboard:read
    """
    query = db.query(UserSession).filter(
        UserSession.user_id == current_user.id
    )
    
    if not include_inactive:
        query = query.filter(
            UserSession.is_active == True,
            UserSession.expires_at > datetime.utcnow()
        )
    
    sessions = query.order_by(desc(UserSession.last_activity)).all()
    
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "platform": s.platform,
                "device_type": s.device_type,
                "device_name": s.device_name,
                "browser": s.browser,
                "os": s.os,
                "ip_address": s.ip_address,
                "location_city": s.location_city,
                "location_country": s.location_country,
                "is_active": s.is_active,
                "last_activity": s.last_activity.isoformat() if s.last_activity else None,
                "created_at": s.created_at.isoformat(),
                "expires_at": s.expires_at.isoformat()
            }
            for s in sessions
        ],
        "active_count": sum(1 for s in sessions if s.is_active and s.expires_at > datetime.utcnow()),
        "total_count": len(sessions)
    }


@router.get("/sessions/{session_id}/sales")
@require_permission("dashboard:read")
def get_session_sales(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Récupère toutes les ventes réalisées lors d'une session spécifique
    Permission requise: dashboard:read
    """
    # Vérifier que la session appartient à l'utilisateur
    session = db.query(UserSession).filter(
        UserSession.session_id == session_id,
        UserSession.user_id == current_user.id
    ).first()
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session non trouvée"
        )
    
    # Construire la requête des ventes
    query = db.query(Sale).filter(
        Sale.created_by == current_user.id,
        Sale.created_at >= session.created_at,
        Sale.created_at <= session.expires_at
    )
    
    if start_date:
        query = query.filter(func.date(Sale.created_at) >= start_date)
    if end_date:
        query = query.filter(func.date(Sale.created_at) <= end_date)
    
    sales = query.order_by(desc(Sale.created_at)).all()
    
    return {
        "session": {
            "session_id": session.session_id,
            "platform": session.platform,
            "device_name": session.device_name,
            "device_type": session.device_type,
            "started_at": session.created_at.isoformat(),
            "ended_at": session.expires_at.isoformat() if not session.is_active else None
        },
        "sales": [
            {
                "id": str(s.id),
                "reference": s.reference,
                "total_amount": float(s.total_price),
                "payment_method": s.payment_method,
                "created_at": s.created_at.isoformat(),
                "items_count": len(s.items) if hasattr(s, 'items') else 0
            }
            for s in sales
        ],
        "summary": {
            "total_sales": len(sales),
            "total_amount": sum(float(s.total_price) for s in sales),
            "average_basket": sum(float(s.total_price) for s in sales) / len(sales) if sales else 0
        }
    }


@router.post("/session/logout")
@require_permission("dashboard:write")
def logout_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    session_id: Optional[str] = Query(None)
):
    """
    Déconnecte une session spécifique ou toutes les sessions
    Permission requise: dashboard:write
    """
    query = db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.is_active == True
    )
    
    if session_id:
        query = query.filter(UserSession.session_id == session_id)
    
    sessions = query.all()
    
    for session in sessions:
        session.is_active = False
    
    db.commit()
    
    return {
        "message": f"{len(sessions)} session(s) déconnectée(s)",
        "sessions_count": len(sessions)
    }


@router.post("/session/{session_id}/activity")
@require_permission("dashboard:write")
def update_session_activity(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Met à jour la dernière activité d'une session
    Permission requise: dashboard:write
    """
    session = db.query(UserSession).filter(
        UserSession.session_id == session_id,
        UserSession.user_id == current_user.id,
        UserSession.is_active == True
    ).first()
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session non trouvée ou inactive"
        )
    
    session.last_activity = datetime.utcnow()
    db.commit()
    
    return {"message": "Activité mise à jour"}


# ===================================================================
# ENDPOINTS ADMINISTRATIFS
# ===================================================================

@router.get("/performance")
@require_permission("dashboard:read")
def get_performance_indicators(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_pharmacy: Optional[Pharmacy] = Depends(get_current_pharmacy_entity),
    period: str = Query("month", regex="^(day|week|month|year)$")
):
    """
    Récupère les indicateurs de performance avancés
    Permission requise: dashboard:read
    """
    tenant_id = current_tenant.id if current_tenant else current_user.tenant_id
    
    today = date.today()
    
    if period == "day":
        start_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
    elif period == "month":
        start_date = today.replace(day=1)
    else:
        start_date = today.replace(month=1, day=1)
    
    # Ventes sur la période
    sales = db.query(
        func.count(Sale.id).label("count"),
        func.sum(Sale.total_price).label("total"),
        func.sum(SaleItem.quantity).label("items")
    ).join(SaleItem).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= start_date,
        Sale.status == "completed"
    ).first()
    
    if current_pharmacy:
        sales = db.query(
            func.count(Sale.id).label("count"),
            func.sum(Sale.total_price).label("total"),
            func.sum(SaleItem.quantity).label("items")
        ).join(SaleItem).filter(
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == current_pharmacy.id,
            Sale.created_at >= start_date,
            Sale.status == "completed"
        ).first()
    
    sales_count = sales.count or 0
    total_sales = sales.total or 0
    items_sold = sales.items or 0
    
    # Coûts sur la période
    total_costs = db.query(func.sum(Cost.amount)).filter(
        Cost.tenant_id == tenant_id,
        Cost.created_at >= start_date
    ).scalar() or 0
    
    # Bénéfice net
    net_profit = total_sales - total_costs
    
    # Nombre de clients uniques
    unique_customers = db.query(func.count(func.distinct(Sale.client_id))).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= start_date,
        Sale.status == "completed"
    ).scalar() or 0
    
    # Panier moyen
    average_basket = total_sales / unique_customers if unique_customers > 0 else 0
    
    # Nombre de produits dans le stock
    product_query = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    )
    if current_pharmacy:
        product_query = product_query.filter(Product.pharmacy_id == current_pharmacy.id)
    total_products = product_query.count()
    
    # Taux de rotation du stock (estimation)
    avg_stock = db.query(func.avg(Product.quantity)).filter(
        Product.tenant_id == tenant_id,
        Product.is_active == True
    ).scalar() or 0
    
    if current_pharmacy:
        avg_stock = db.query(func.avg(Product.quantity)).filter(
            Product.tenant_id == tenant_id,
            Product.pharmacy_id == current_pharmacy.id,
            Product.is_active == True
        ).scalar() or 0
    
    stock_turnover = (items_sold / avg_stock) if avg_stock > 0 else 0
    
    return {
        "period": period,
        "start_date": start_date.isoformat(),
        "sales": {
            "count": sales_count,
            "total": float(total_sales),
            "items_sold": items_sold,
            "average_basket": float(average_basket)
        },
        "customers": {
            "unique": unique_customers,
            "conversion_rate": round((unique_customers / sales_count * 100), 2) if sales_count > 0 else 0
        },
        "profitability": {
            "total_costs": float(total_costs),
            "net_profit": float(net_profit),
            "profit_margin": round((net_profit / total_sales * 100), 2) if total_sales > 0 else 0
        },
        "inventory": {
            "total_products": total_products,
            "stock_turnover": round(stock_turnover, 2),
            "average_stock": float(avg_stock)
        }
    }