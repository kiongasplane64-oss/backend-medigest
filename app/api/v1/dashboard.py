# app/api/v1/endpoints/dashboard.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract
from datetime import datetime, timedelta, date
from typing import Optional, List
from app.db.session import get_db
from app.models.user import User
from app.models.sale import Sale
from app.models.cost import Cost
from app.models.product import Product
from app.models.transfert import TransferStatus
from app.models.tenant import Tenant
from app.models.inventory_alert import InventoryAlert
from app.core.security import get_current_user, require_roles

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

@router.get("/stats")
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    pharmacy_id: Optional[int] = Query(None, description="ID de la pharmacie pour les admins")
):
    """
    Retourne les statistiques complètes pour le dashboard
    """
    # Déterminer le tenant_id à utiliser
    tenant_id = pharmacy_id if pharmacy_id and current_user.role in ['admin', 'super_admin'] else current_user.tenant_id
    
    # Date du jour
    today = date.today()
    first_day_month = today.replace(day=1)
    
    # === VENTES ===
    # Ventes du jour
    daily_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        func.date(Sale.created_at) == today
    ).scalar() or 0
    
    # Ventes du mois
    monthly_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= first_day_month
    ).scalar() or 0
    
    # Ventes hier pour calculer la tendance
    yesterday_sales = db.query(func.sum(Sale.total_price)).filter(
        Sale.tenant_id == tenant_id,
        func.date(Sale.created_at) == today - timedelta(days=1)
    ).scalar() or 0
    
    # Tendance (pourcentage)
    sales_trend = 0
    if yesterday_sales > 0:
        sales_trend = ((daily_sales - yesterday_sales) / yesterday_sales) * 100
    
    # === PRODUITS ===
    # Total produits
    total_products = db.query(func.count(Product.id)).filter(
        Product.tenant_id == tenant_id
    ).scalar() or 0
    
    # Produits en rupture (quantité = 0)
    out_of_stock_count = db.query(func.count(Product.id)).filter(
        Product.tenant_id == tenant_id,
        Product.quantity == 0
    ).scalar() or 0
    
    # Produits en stock bas (quantité <= seuil d'alerte)
    low_stock_count = db.query(func.count(Product.id)).filter(
        Product.tenant_id == tenant_id,
        Product.quantity <= Product.alert_threshold
    ).scalar() or 0
    
    # Produits expirés
    expired_count = db.query(func.count(Product.id)).filter(
        Product.tenant_id == tenant_id,
        Product.expiry_date < today
    ).scalar() or 0
    
    # Produits expirant bientôt (30 jours)
    expiring_soon_count = db.query(func.count(Product.id)).filter(
        Product.tenant_id == tenant_id,
        Product.expiry_date >= today,
        Product.expiry_date <= today + timedelta(days=30)
    ).scalar() or 0
    
    # === VALEURS FINANCIÈRES ===
    # Valeur totale du stock (prix de vente)
    total_stock_value = db.query(func.sum(Product.selling_price * Product.quantity)).filter(
        Product.tenant_id == tenant_id
    ).scalar() or 0
    
    # Valeur d'achat totale
    total_purchase_value = db.query(func.sum(Product.purchase_price * Product.quantity)).filter(
        Product.tenant_id == tenant_id
    ).scalar() or 0
    
    # Bénéfice potentiel
    potential_profit = total_stock_value - total_purchase_value
    
    # Bénéfice net du mois (ventes - coûts)
    monthly_costs = db.query(func.sum(Cost.amount)).filter(
        Cost.tenant_id == tenant_id,
        Cost.created_at >= first_day_month
    ).scalar() or 0
    
    net_profit = monthly_sales - monthly_costs
    
    # === UTILISATEURS ===
    active_users = db.query(func.count(User.id)).filter(
        User.tenant_id == tenant_id,
        User.is_active == True
    ).scalar() or 0
    
    # === TENANT (ABONNEMENT) ===
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    # === ALERTES ===
    alerts = db.query(InventoryAlert).filter(
        InventoryAlert.tenant_id == tenant_id,
        InventoryAlert.is_resolved == False
    ).limit(5).all()
    
    alert_list = []
    for alert in alerts:
        product = db.query(Product).filter(Product.id == alert.product_id).first()
        alert_list.append({
            "id": alert.id,
            "type": alert.alert_type,
            "product": product.name if product else "Produit inconnu",
            "quantity": product.quantity if product else 0,
            "threshold": product.alert_threshold if product else 0,
            "expiry_date": product.expiry_date.isoformat() if product and product.expiry_date else None,
            "created_at": alert.created_at.isoformat()
        })
    
    # === HISTORIQUE DES VENTES (30 derniers jours) ===
    sales_history = []
    for i in range(30):
        day = today - timedelta(days=i)
        day_sales = db.query(func.count(Sale.id), func.sum(Sale.total_price)).filter(
            Sale.tenant_id == tenant_id,
            func.date(Sale.created_at) == day
        ).first()
        
        sales_history.append({
            "date": day.isoformat(),
            "count": day_sales[0] or 0,
            "amount": day_sales[1] or 0
        })
    
    return {
        "daily_sales": daily_sales,
        "monthly_sales": monthly_sales,
        "sales_trend": round(sales_trend, 2),
        "total_products": total_products,
        "out_of_stock_count": out_of_stock_count,
        "low_stock_count": low_stock_count,
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "total_stock_value": total_stock_value,
        "total_purchase_value": total_purchase_value,
        "potential_profit": potential_profit,
        "net_profit": net_profit,
        "active_users": active_users,
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "plan_name": tenant.plan_name,
            "max_users": tenant.max_users,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None
        } if tenant else None,
        "alerts": alert_list
    }

@router.get("/alerts")
def get_inventory_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=100)
):
    """
    Récupère les alertes d'inventaire
    """
    tenant_id = current_user.tenant_id
    
    # Alertes non résolues
    alerts = db.query(InventoryAlert).filter(
        InventoryAlert.tenant_id == tenant_id,
        InventoryAlert.is_resolved == False
    ).order_by(InventoryAlert.created_at.desc()).limit(limit).all()
    
    result = []
    for alert in alerts:
        product = db.query(Product).filter(Product.id == alert.product_id).first()
        result.append({
            "id": alert.id,
            "type": alert.alert_type,
            "severity": alert.severity,
            "message": alert.message,
            "product_id": alert.product_id,
            "product_name": product.name if product else None,
            "created_at": alert.created_at.isoformat(),
            "is_resolved": alert.is_resolved
        })
    
    return {"alerts": result}

@router.get("/sales/trends")
def get_sales_trends(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    period: str = Query("week", regex="^(day|week|month|year)$")
):
    """
    Retourne les tendances des ventes
    """
    tenant_id = current_user.tenant_id
    today = date.today()
    
    if period == "day":
        # Ventes par heure aujourd'hui
        start_date = today
        group_by = extract('hour', Sale.created_at)
    elif period == "week":
        # Ventes par jour cette semaine
        start_date = today - timedelta(days=today.weekday())
        group_by = func.date(Sale.created_at)
    elif period == "month":
        # Ventes par jour ce mois
        start_date = today.replace(day=1)
        group_by = func.date(Sale.created_at)
    else:  # year
        # Ventes par mois cette année
        start_date = today.replace(month=1, day=1)
        group_by = extract('month', Sale.created_at)
    
    results = db.query(
        group_by.label('period'),
        func.count(Sale.id).label('count'),
        func.sum(Sale.total_price).label('amount')
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.created_at >= start_date
    ).group_by(group_by).order_by(group_by).all()
    
    return [{
        "period": str(r.period),
        "count": r.count,
        "amount": float(r.amount) if r.amount else 0
    } for r in results]

@router.get("/products/categories")
def get_products_by_category(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Distribution des produits par catégorie
    """
    tenant_id = current_user.tenant_id
    
    results = db.query(
        Product.category,
        func.count(Product.id).label('count'),
        func.sum(Product.quantity).label('total_quantity'),
        func.sum(Product.selling_price * Product.quantity).label('total_value')
    ).filter(
        Product.tenant_id == tenant_id
    ).group_by(Product.category).all()
    
    return [{
        "category": r.category,
        "count": r.count,
        "total_quantity": r.total_quantity or 0,
        "total_value": float(r.total_value) if r.total_value else 0
    } for r in results]


@router.get("/overview")
def dashboard_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retourne un dashboard complet selon le rôle.
    """
    tenant_id = current_user.tenant_id
    role = current_user.role

    # === Admin : toutes les données ===
    if role == "admin":
        # Total utilisateurs
        total_users = db.query(User).filter(User.tenant_id == tenant_id).count()

        # Total ventes et bénéfice
        total_sales = db.query(Sale).filter(Sale.tenant_id == tenant_id).all()
        chiffre_affaire = sum(s.total_price for s in total_sales)
        cout_achats = sum(s.cost_price for s in total_sales)
        benefice = chiffre_affaire - cout_achats

        # Historique ventes (derniers 30 jours)
        today = datetime.utcnow().date()
        sales_history = []
        for i in range(30):
            day = today - timedelta(days=i)
            day_sales = db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.date_creation.cast("date") == day
            ).all()
            sales_history.append({
                "date": str(day),
                "ventes": len(day_sales),
                "chiffre_affaire": sum(s.total_price for s in day_sales)
            })

        # Dépenses
        total_costs = db.query(Cost).filter(Cost.tenant_id == tenant_id).all()
        total_depenses = sum(c.amount for c in total_costs)

        # Retour produits
        total_returns = db.query(Product).filter(Product.tenant_id == tenant_id, Product.returned == True).count()

        return {
            "role": role,
            "total_users": total_users,
            "chiffre_affaire": chiffre_affaire,
            "benefice": benefice,
            "total_depenses": total_depenses,
            "total_retours": total_returns,
            "sales_history": sales_history
        }

    # === Manager : données limitées ===
    elif role == "manager":
        # Exemple : ventes, stock, bénéfice
        return {"message": "Dashboard manager : ventes et stock"}

    # === Pharmacist ===
    elif role == "pharmacist":
        return {"message": "Dashboard pharmacien : ventes et prescriptions"}

    # === Cashier ===
    elif role == "cashier":
        return {"message": "Dashboard caissier : ventes et paiements"}

    # === Accountant ===
    elif role == "accountant":
        return {"message": "Dashboard comptable : dettes, paiements, budgets"}

    else:
        return {"message": "Dashboard limité pour rôle inconnu"}
