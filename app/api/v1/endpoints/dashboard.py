# app/api/v1/endpoints/dashboard.py
"""
API de statistiques pour le tableau de bord principal (Dashboard.tsx)
Fournit toutes les données nécessaires à l'affichage des KPI, graphiques et alertes
Basé sur la BRANCHE de l'utilisateur connecté
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_, extract, case, text
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
from decimal import Decimal
import logging

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.branch import Branch
from app.models.tenant import Tenant
from app.models.user_branch import UserBranch
from app.models.stock_movement import StockMovement
from app.models.finance import Expense
from app.models.customer import Customer
from app.models.cost import Supplier
from app.models.purchase import Purchase, PurchaseItem
from app.models.debt import Debt
from app.models.return_product import Return, ReturnItem, ReturnStatus, ReturnType
from app.models.transfert import ProductTransfer, TransferStatus
from app.api.deps import (
    get_current_tenant,
    get_current_user,
    get_current_active_user,
    get_current_pharmacy_entity,
    require_permission,
    can_user_access_pharmacy
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = logging.getLogger(__name__)


# =======================
# TYPES ET SCHEMAS
# =======================

from pydantic import BaseModel
from typing import Optional


class DashboardFilters(BaseModel):
    """Filtres pour le dashboard (uniquement branch_id)"""
    branch_id: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class DashboardBranchInfo(BaseModel):
    """Informations sur la branche active"""
    id: str
    name: str
    address: str
    phone: Optional[str] = None
    email: Optional[str] = None
    city: str
    parent_pharmacy_id: str
    parent_pharmacy_name: str
    manager_name: Optional[str] = None


class DashboardStatsResponse(BaseModel):
    """Structure complète des statistiques du dashboard"""
    # Informations branche
    branch_info: Optional[DashboardBranchInfo] = None
    
    # Ventes
    daily_sales: float
    monthly_sales: float
    sales_trend: float
    daily_transactions: int
    monthly_transactions: int
    
    # Stock
    total_products: int
    out_of_stock_count: int
    low_stock_count: int
    expired_count: int
    expiring_soon_count: int
    total_stock_value: float
    total_purchase_value: float
    potential_profit: float
    stock_turnover: float
    
    # Bénéfices
    net_profit: float
    daily_profit: float
    
    # Clients
    total_customers: int
    
    # Dépenses
    monthly_expenses: float
    daily_expenses: float
    expense_categories: List[Dict[str, Any]]
    
    # Dettes
    monthly_debts: float
    total_debts: float
    unpaid_debts: float
    recovery_rate: float
    debt_list: List[Dict[str, Any]]
    
    # Achats
    monthly_purchases: float
    daily_purchases: float
    suppliers_count: int
    pending_orders: int
    recent_purchases: List[Dict[str, Any]]
    
    # Retours
    monthly_returns: float
    pending_returns: int
    total_returns_value: float
    
    # Transferts
    pending_transfers: int
    in_transit_transfers: int
    
    # Transactions récentes
    recent_transactions: List[Dict[str, Any]]
    
    # Alertes stock
    low_stock_products: List[Dict[str, Any]]
    expiring_products: List[Dict[str, Any]]
    
    # Métriques additionnelles
    active_users: int
    daily_sales_count: int
    monthly_sales_count: int
    
    # Période
    period_start: Optional[str] = None
    period_end: Optional[str] = None


class DashboardAlertResponse(BaseModel):
    """Structure pour les alertes"""
    id: Optional[str]
    type: str
    severity: str
    title: str
    message: str
    product_id: Optional[str]
    product_name: Optional[str]
    current_stock: Optional[int]
    threshold: Optional[int]
    expiry_date: Optional[str]
    days_remaining: Optional[int]


class DashboardAlertListResponse(BaseModel):
    """Liste des alertes"""
    alerts: List[DashboardAlertResponse]
    total: int
    critical_count: int
    warning_count: int


# =======================
# HELPERS
# =======================

def get_user_current_branch(
    db: Session, 
    user_id: UUID, 
    tenant_id: Optional[UUID] = None,
    branch_id: Optional[UUID] = None
) -> Optional[Branch]:
    """
    Récupère la branche actuelle de l'utilisateur connecté.
    Priorité:
    1. Si branch_id est fourni et que l'utilisateur y a accès
    2. La branche active stockée dans user.active_branch_id
    3. La branche principale de l'utilisateur
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return None
    
    # Super admin ou admin peut accéder à n'importe quelle branche
    is_admin = user.role in ["super_admin", "superadmin", "admin"]
    
    # Si un branch_id est fourni, vérifier l'accès
    if branch_id:
        if is_admin:
            branch = db.query(Branch).filter(
                Branch.id == branch_id,
                Branch.tenant_id == tenant_id,
                Branch.is_active == True
            ).first()
            if branch:
                return branch
        else:
            # Vérifier que l'utilisateur est assigné à cette branche
            user_branch = db.query(UserBranch).filter(
                UserBranch.user_id == user_id,
                UserBranch.branch_id == branch_id,
                UserBranch.is_active == True
            ).first()
            if user_branch:
                return user_branch.branch
    
    # Sinon, utiliser la branche active stockée
    if user.active_branch_id:
        branch = db.query(Branch).filter(
            Branch.id == user.active_branch_id,
            Branch.is_active == True
        ).first()
        if branch and (is_admin or _user_has_branch_access(db, user_id, branch.id)):
            return branch
    
    # Sinon, récupérer la première branche assignée
    user_branch = db.query(UserBranch).filter(
        UserBranch.user_id == user_id,
        UserBranch.is_active == True
    ).first()
    
    if user_branch:
        return user_branch.branch
    
    return None


def _user_has_branch_access(db: Session, user_id: UUID, branch_id: UUID) -> bool:
    """Vérifie si l'utilisateur a accès à la branche"""
    return db.query(UserBranch).filter(
        UserBranch.user_id == user_id,
        UserBranch.branch_id == branch_id,
        UserBranch.is_active == True
    ).first() is not None


def safe_decimal_to_float(value: Any, default: float = 0.0) -> float:
    """Convertit Decimal en float de manière sécurisée"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """Convertit en entier de manière sécurisée"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_trend(current: float, previous: float) -> float:
    """Calcule le pourcentage de tendance"""
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


def get_date_range(period: str = "month"):
    """Retourne les dates de début et fin selon la période"""
    today = date.today()
    
    if period == "day":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == "month":
        start_date = today.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1) - timedelta(days=1)
    else:
        start_date = today - timedelta(days=30)
        end_date = today
    
    return start_date, end_date


# =======================
# ROUTES PRINCIPALES
# =======================

@router.get("/stats", response_model=DashboardStatsResponse)
async def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    period: str = Query("month", description="Période: day, week, month"),
    start_date: Optional[date] = Query(None, description="Date de début personnalisée"),
    end_date: Optional[date] = Query(None, description="Date de fin personnalisée")
):
    """
    Récupère toutes les statistiques pour le tableau de bord principal.
    Les données sont filtrées UNIQUEMENT par la branche de l'utilisateur connecté.
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return _get_empty_stats_response()
        
        # Récupérer la pharmacie parente pour les informations
        parent_pharmacy = branch.parent_pharmacy
        
        # Construire les informations de la branche
        branch_info = DashboardBranchInfo(
            id=str(branch.id),
            name=branch.name,
            address=branch.address,
            phone=branch.phone,
            email=branch.email,
            city=branch.city,
            parent_pharmacy_id=str(parent_pharmacy.id) if parent_pharmacy else "",
            parent_pharmacy_name=parent_pharmacy.name if parent_pharmacy else "",
            manager_name=branch.manager_name
        )
        
        # Déterminer les plages de dates
        if start_date and end_date:
            current_start = start_date
            current_end = end_date
        else:
            current_start, current_end = get_date_range(period)
        
        # Date précédente pour la comparaison
        days_diff = (current_end - current_start).days + 1
        previous_start = current_start - timedelta(days=days_diff)
        previous_end = current_start - timedelta(days=1)
        
        # Convertir en datetime
        current_start_dt = datetime.combine(current_start, datetime.min.time())
        current_end_dt = datetime.combine(current_end, datetime.max.time())
        previous_start_dt = datetime.combine(previous_start, datetime.min.time())
        previous_end_dt = datetime.combine(previous_end, datetime.max.time())
        
        # 1. STATISTIQUES DES VENTES
        sales_stats_current = _get_sales_stats_by_branch(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        sales_stats_previous = _get_sales_stats_by_branch(
            db, tenant_id, branch.id, previous_start_dt, previous_end_dt
        )
        
        daily_sales = sales_stats_current["daily_sales"]
        monthly_sales = sales_stats_current["monthly_sales"]
        sales_trend = calculate_trend(monthly_sales, sales_stats_previous["monthly_sales"])
        daily_transactions = sales_stats_current["daily_transactions"]
        monthly_transactions = sales_stats_current["monthly_transactions"]
        daily_profit = sales_stats_current["daily_profit"]
        net_profit = sales_stats_current["net_profit"]
        
        # 2. STATISTIQUES DU STOCK
        stock_stats = _get_stock_stats_by_branch(db, tenant_id, branch.id)
        
        # 3. STATISTIQUES DES DÉPENSES
        expense_stats = _get_expense_stats_by_branch(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        
        # 4. STATISTIQUES DES DETTES
        debt_stats = _get_debt_stats_by_branch(db, tenant_id, branch.id)
        
        # 5. STATISTIQUES DES ACHATS
        purchase_stats = _get_purchase_stats_by_branch(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        
        # 6. STATISTIQUES DES RETOURS
        return_stats = _get_return_stats_by_branch(
            db, tenant_id, branch.id, current_start_dt, current_end_dt
        )
        
        # 7. STATISTIQUES DES TRANSFERTS
        transfer_stats = _get_transfer_stats_by_branch(db, tenant_id, branch.id)
        
        # 8. TRANSACTIONS RÉCENTES
        recent_transactions = _get_recent_transactions_by_branch(
            db, tenant_id, branch.id, limit=10
        )
        
        # 9. PRODUITS EN STOCK BAS
        low_stock_products = _get_low_stock_products_by_branch(
            db, tenant_id, branch.id, limit=10
        )
        
        # 10. PRODUITS EXPIRANT BIENTÔT
        expiring_products = _get_expiring_products_by_branch(
            db, tenant_id, branch.id, limit=10
        )
        
        # 11. DETTES RÉCENTES
        debt_list = _get_recent_debts_by_branch(
            db, tenant_id, branch.id, limit=10
        )
        
        # 12. ACHATS RÉCENTS
        recent_purchases = _get_recent_purchases_by_branch(
            db, tenant_id, branch.id, limit=10
        )
        
        # 13. CATÉGORIES DE DÉPENSES
        expense_categories = _get_expense_categories_by_branch(
            db, tenant_id, branch.id, current_start_dt, current_end_dt, limit=5
        )
        
        # 14. UTILISATEURS ACTIFS
        active_users = _get_active_users_count_by_branch(
            db, tenant_id, branch.id
        )
        
        # 15. CLIENTS TOTAUX
        total_customers = _get_customers_count_by_branch(
            db, tenant_id, branch.id
        )
        
        return DashboardStatsResponse(
            branch_info=branch_info,
            
            # Ventes
            daily_sales=daily_sales,
            monthly_sales=monthly_sales,
            sales_trend=sales_trend,
            daily_transactions=daily_transactions,
            monthly_transactions=monthly_transactions,
            daily_sales_count=daily_transactions,
            monthly_sales_count=monthly_transactions,
            
            # Stock
            total_products=stock_stats["total_products"],
            out_of_stock_count=stock_stats["out_of_stock_count"],
            low_stock_count=stock_stats["low_stock_count"],
            expired_count=stock_stats["expired_count"],
            expiring_soon_count=stock_stats["expiring_soon_count"],
            total_stock_value=stock_stats["total_selling_value"],
            total_purchase_value=stock_stats["total_purchase_value"],
            potential_profit=stock_stats["potential_profit"],
            stock_turnover=stock_stats["stock_turnover"],
            
            # Bénéfices
            net_profit=net_profit,
            daily_profit=daily_profit,
            
            # Clients
            total_customers=total_customers,
            
            # Dépenses
            monthly_expenses=expense_stats["monthly_expenses"],
            daily_expenses=expense_stats["daily_expenses"],
            expense_categories=expense_categories,
            
            # Dettes
            monthly_debts=debt_stats["monthly_debts"],
            total_debts=debt_stats["total_debts"],
            unpaid_debts=debt_stats["unpaid_debts"],
            recovery_rate=debt_stats["recovery_rate"],
            debt_list=debt_list,
            
            # Achats
            monthly_purchases=purchase_stats["monthly_purchases"],
            daily_purchases=purchase_stats["daily_purchases"],
            suppliers_count=purchase_stats["suppliers_count"],
            pending_orders=purchase_stats["pending_orders"],
            recent_purchases=recent_purchases,
            
            # Retours
            monthly_returns=return_stats["monthly_returns"],
            pending_returns=return_stats["pending_returns"],
            total_returns_value=return_stats["total_returns_value"],
            
            # Transferts
            pending_transfers=transfer_stats["pending_transfers"],
            in_transit_transfers=transfer_stats["in_transit_transfers"],
            
            # Transactions récentes
            recent_transactions=recent_transactions,
            
            # Alertes stock
            low_stock_products=low_stock_products,
            expiring_products=expiring_products,
            
            # Métriques additionnelles
            active_users=active_users,
            
            # Période
            period_start=current_start.isoformat(),
            period_end=current_end.isoformat()
        )
        
    except Exception as e:
        logger.error(f"Erreur récupération statistiques dashboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération statistiques: {str(e)}"
        )


@router.get("/alerts", response_model=DashboardAlertListResponse)
async def get_dashboard_alerts(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    severity: Optional[str] = Query(None, description="high, medium, low"),
    include_resolved: bool = Query(False, description="Inclure les alertes résolues"),
    limit: int = Query(50, ge=1, le=200)
):
    """
    Récupère toutes les alertes pour le tableau de bord:
    - Stock critique (rupture, stock bas)
    - Péremption (expiré, expirant bientôt)
    (Basé sur la branche de l'utilisateur)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return DashboardAlertListResponse(alerts=[], total=0, critical_count=0, warning_count=0)
        
        alerts = []
        critical_count = 0
        warning_count = 0
        
        # 1. ALERTES DE STOCK CRITIQUE (rupture)
        out_of_stock_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.quantity == 0
        ).all()
        
        for product in out_of_stock_products:
            alerts.append(DashboardAlertResponse(
                id=str(product.id),
                type="out_of_stock",
                severity="high",
                title="Rupture de stock",
                message=f"{product.name} est en rupture de stock",
                product_id=str(product.id),
                product_name=product.name,
                current_stock=0,
                threshold=product.alert_threshold,
                expiry_date=None,
                days_remaining=None
            ))
            critical_count += 1
        
        # 2. ALERTES DE STOCK BAS
        low_stock_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.quantity > 0,
            Product.quantity <= Product.alert_threshold
        ).all()
        
        for product in low_stock_products:
            if product.quantity == 0:
                continue
            severity = "high" if product.quantity <= product.alert_threshold / 2 else "medium"
            alerts.append(DashboardAlertResponse(
                id=str(product.id),
                type="low_stock",
                severity=severity,
                title="Stock faible",
                message=f"{product.name} n'a plus que {product.quantity} unités (seuil: {product.alert_threshold})",
                product_id=str(product.id),
                product_name=product.name,
                current_stock=product.quantity,
                threshold=product.alert_threshold,
                expiry_date=None,
                days_remaining=None
            ))
            if severity == "high":
                critical_count += 1
            else:
                warning_count += 1
        
        # 3. ALERTES DE PÉREMPTION
        today = date.today()
        expiry_threshold = today + timedelta(days=30)
        
        expiring_products = db.query(Product).filter(
            Product.tenant_id == tenant_id,
            Product.branch_id == branch.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date <= expiry_threshold,
            Product.quantity > 0
        ).all()
        
        for product in expiring_products:
            days_remaining = (product.expiry_date - today).days
            if days_remaining < 0:
                severity = "high"
                title = "Produit expiré"
                message = f"{product.name} est expiré depuis le {product.expiry_date}"
            elif days_remaining <= 7:
                severity = "high"
                title = "Expiration imminente"
                message = f"{product.name} expire dans {days_remaining} jours"
            else:
                severity = "medium"
                title = "Expiration bientôt"
                message = f"{product.name} expire dans {days_remaining} jours"
            
            alerts.append(DashboardAlertResponse(
                id=str(product.id),
                type="expiry",
                severity=severity,
                title=title,
                message=message,
                product_id=str(product.id),
                product_name=product.name,
                current_stock=product.quantity,
                threshold=product.alert_threshold,
                expiry_date=product.expiry_date.isoformat(),
                days_remaining=days_remaining
            ))
            if severity == "high":
                critical_count += 1
            else:
                warning_count += 1
        
        # Filtrer par sévérité si demandé
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        
        # Limiter le nombre d'alertes
        alerts = alerts[:limit]
        
        return DashboardAlertListResponse(
            alerts=alerts,
            total=len(alerts),
            critical_count=critical_count,
            warning_count=warning_count
        )
        
    except Exception as e:
        logger.error(f"Erreur récupération alertes dashboard: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération alertes: {str(e)}"
        )


@router.get("/stock-value-history")
async def get_stock_value_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    days: int = Query(30, ge=7, le=365)
):
    """
    Récupère l'historique de la valeur du stock sur une période.
    (Basé sur la branche de l'utilisateur)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return {"history": [], "total_stock_value": 0}
        
        history = []
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Pour chaque jour, calculer la valeur du stock
        current_date = start_date
        while current_date <= end_date:
            products = db.query(Product).filter(
                Product.tenant_id == tenant_id,
                Product.branch_id == branch.id,
                Product.is_active == True,
                Product.created_at <= datetime.combine(current_date, datetime.max.time())
            ).all()
            
            total_value = 0
            for product in products:
                total_value += safe_decimal_to_float(product.selling_price) * (product.quantity or 0)
            
            history.append({
                "date": current_date.isoformat(),
                "value": round(total_value, 2)
            })
            
            current_date += timedelta(days=1)
        
        return {
            "history": history,
            "total_stock_value": history[-1]["value"] if history else 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur historique valeur stock: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur historique: {str(e)}"
        )


@router.get("/sales-history")
async def get_sales_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    days: int = Query(30, ge=7, le=365)
):
    """
    Récupère l'historique des ventes sur une période.
    (Basé sur la branche de l'utilisateur)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return {"history": [], "total_revenue": 0, "total_sales": 0}
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Requête groupée par jour
        results = db.query(
            func.date(Sale.created_at).label("sale_date"),
            func.count(Sale.id).label("sales_count"),
            func.coalesce(func.sum(Sale.total_amount), 0).label("total_revenue"),
            func.coalesce(func.avg(Sale.total_amount), 0).label("average_basket")
        ).filter(
            Sale.tenant_id == tenant_id,
            Sale.branch_id == branch.id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= start_date,
            func.date(Sale.created_at) <= end_date
        ).group_by(func.date(Sale.created_at)).order_by(func.date(Sale.created_at)).all()
        
        history = []
        total_revenue = 0
        total_sales = 0
        
        for row in results:
            history.append({
                "date": row.sale_date.isoformat(),
                "sales_count": row.sales_count,
                "total_revenue": safe_decimal_to_float(row.total_revenue),
                "average_basket": safe_decimal_to_float(row.average_basket)
            })
            total_revenue += safe_decimal_to_float(row.total_revenue)
            total_sales += row.sales_count
        
        return {
            "history": history,
            "total_revenue": total_revenue,
            "total_sales": total_sales,
            "average_daily_revenue": total_revenue / days if days > 0 else 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur historique ventes: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur historique ventes: {str(e)}"
        )


@router.get("/profit-history")
async def get_profit_history(
    db: Session = Depends(get_db),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    branch_id: Optional[UUID] = Query(None, description="Filtrer par succursale"),
    days: int = Query(30, ge=7, le=365)
):
    """
    Récupère l'historique des bénéfices sur une période.
    (Basé sur la branche de l'utilisateur)
    """
    try:
        tenant_id = current_tenant.id if current_tenant else None
        
        # Déterminer la branche de l'utilisateur
        branch = get_user_current_branch(db, current_user.id, tenant_id, branch_id)
        
        if not branch:
            return {"history": [], "total_profit": 0, "average_profit": 0}
        
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        history = []
        total_profit = 0
        
        for i in range(days):
            current_date = start_date + timedelta(days=i)
            start_dt = datetime.combine(current_date, datetime.min.time())
            end_dt = datetime.combine(current_date, datetime.max.time())
            
            # Récupérer les ventes du jour
            sales = db.query(Sale).filter(
                Sale.tenant_id == tenant_id,
                Sale.branch_id == branch.id,
                Sale.status == "completed",
                Sale.created_at >= start_dt,
                Sale.created_at <= end_dt
            ).all()
            
            sale_ids = [s.id for s in sales]
            
            if sale_ids:
                # Calculer le coût des ventes
                sale_items = db.query(SaleItem).filter(
                    SaleItem.sale_id.in_(sale_ids),
                    SaleItem.tenant_id == tenant_id
                ).all()
                
                total_revenue = Decimal('0')
                total_cost = Decimal('0')
                
                for item in sale_items:
                    product = db.query(Product).filter(
                        Product.id == item.product_id,
                        Product.tenant_id == tenant_id
                    ).first()
                    
                    revenue = Decimal(str(item.total))
                    cost = Decimal(str(product.purchase_price)) * Decimal(str(item.quantity)) if product else Decimal('0')
                    
                    total_revenue += revenue
                    total_cost += cost
                
                daily_profit = float(total_revenue - total_cost)
            else:
                daily_profit = 0
            
            history.append({
                "date": current_date.isoformat(),
                "profit": round(daily_profit, 2)
            })
            total_profit += daily_profit
        
        return {
            "history": history,
            "total_profit": round(total_profit, 2),
            "average_profit": round(total_profit / days, 2) if days > 0 else 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat()
        }
        
    except Exception as e:
        logger.error(f"Erreur historique bénéfices: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur historique bénéfices: {str(e)}"
        )


# =======================
# FONCTIONS INTERNES - BASÉES SUR BRANCH
# =======================

def _get_empty_stats_response() -> DashboardStatsResponse:
    """Retourne une réponse vide pour les statistiques"""
    return DashboardStatsResponse(
        daily_sales=0,
        monthly_sales=0,
        sales_trend=0,
        daily_transactions=0,
        monthly_transactions=0,
        total_products=0,
        out_of_stock_count=0,
        low_stock_count=0,
        expired_count=0,
        expiring_soon_count=0,
        total_stock_value=0,
        total_purchase_value=0,
        potential_profit=0,
        net_profit=0,
        daily_profit=0,
        total_customers=0,
        monthly_expenses=0,
        daily_expenses=0,
        expense_categories=[],
        monthly_debts=0,
        total_debts=0,
        unpaid_debts=0,
        recovery_rate=0,
        debt_list=[],
        monthly_purchases=0,
        daily_purchases=0,
        suppliers_count=0,
        pending_orders=0,
        recent_purchases=[],
        monthly_returns=0,
        pending_returns=0,
        total_returns_value=0,
        pending_transfers=0,
        in_transit_transfers=0,
        recent_transactions=[],
        low_stock_products=[],
        expiring_products=[],
        active_users=0,
        daily_sales_count=0,
        monthly_sales_count=0,
        stock_turnover=0
    )


def _get_sales_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Récupère les statistiques des ventes pour une branche spécifique"""
    
    query = db.query(
        func.coalesce(func.sum(Sale.total_amount), 0).label("total_sales"),
        func.count(Sale.id).label("sales_count")
    ).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= start_dt,
        Sale.created_at <= end_dt
    )
    
    result = query.first()
    
    total_sales = safe_decimal_to_float(result.total_sales if result else 0)
    sales_count = safe_int(result.sales_count if result else 0)
    
    # Calcul du bénéfice net
    sale_ids = db.query(Sale.id).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= start_dt,
        Sale.created_at <= end_dt
    ).all()
    
    sale_ids_list = [s.id for s in sale_ids]
    
    net_profit = 0
    if sale_ids_list:
        sale_items = db.query(SaleItem).filter(
            SaleItem.sale_id.in_(sale_ids_list),
            SaleItem.tenant_id == tenant_id
        ).all()
        
        total_cost = Decimal('0')
        for item in sale_items:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == tenant_id
            ).first()
            if product:
                total_cost += Decimal(str(product.purchase_price)) * Decimal(str(item.quantity))
        
        net_profit = safe_decimal_to_float(Decimal(str(total_sales)) - total_cost)
    
    days_diff = max(1, (end_dt - start_dt).days)
    
    return {
        "daily_sales": total_sales,
        "monthly_sales": total_sales,
        "daily_transactions": sales_count,
        "monthly_transactions": sales_count,
        "daily_profit": net_profit / days_diff,
        "net_profit": net_profit
    }


def _get_stock_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> Dict[str, Any]:
    """Récupère les statistiques du stock pour une branche spécifique"""
    
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True
    ).all()
    
    total_purchase_value = 0
    total_selling_value = 0
    out_of_stock_count = 0
    low_stock_count = 0
    expired_count = 0
    expiring_soon_count = 0
    total_quantity = 0
    
    today = date.today()
    expiry_threshold = today + timedelta(days=30)
    
    for product in products:
        qty = product.quantity or 0
        total_quantity += qty
        total_purchase_value += safe_decimal_to_float(product.purchase_price) * qty
        total_selling_value += safe_decimal_to_float(product.selling_price) * qty
        
        if qty == 0:
            out_of_stock_count += 1
        elif qty <= (product.alert_threshold or 5):
            low_stock_count += 1
        
        if product.expiry_date:
            if product.expiry_date < today:
                expired_count += 1
            elif product.expiry_date <= expiry_threshold:
                expiring_soon_count += 1
    
    potential_profit = total_selling_value - total_purchase_value
    
    # Taux de rotation
    stock_turnover = 0
    if total_quantity > 0:
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        total_sold = db.query(func.coalesce(func.sum(SaleItem.quantity), 0)).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            SaleItem.tenant_id == tenant_id,
            SaleItem.branch_id == branch_id,
            Sale.status == "completed",
            Sale.created_at >= thirty_days_ago
        ).scalar() or 0
        
        stock_turnover = round(total_sold / total_quantity, 2) if total_quantity > 0 else 0
    
    return {
        "total_products": len(products),
        "total_purchase_value": round(total_purchase_value, 2),
        "total_selling_value": round(total_selling_value, 2),
        "potential_profit": round(potential_profit, 2),
        "out_of_stock_count": out_of_stock_count,
        "low_stock_count": low_stock_count,
        "expired_count": expired_count,
        "expiring_soon_count": expiring_soon_count,
        "stock_turnover": stock_turnover
    }


def _get_expense_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Récupère les statistiques des dépenses pour une branche spécifique"""
    
    # Dépenses du mois
    expenses = db.query(Expense).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.approval_status == "approved",
        Expense.expense_date >= start_dt.date(),
        Expense.expense_date <= end_dt.date()
    ).all()
    
    monthly_expenses = sum(safe_decimal_to_float(e.amount) for e in expenses)
    
    # Dépenses du jour
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    daily_expenses = db.query(Expense).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.approval_status == "approved",
        Expense.created_at >= today_start,
        Expense.created_at <= today_end
    ).all()
    
    daily_expenses_sum = sum(safe_decimal_to_float(e.amount) for e in daily_expenses)
    
    return {
        "monthly_expenses": round(monthly_expenses, 2),
        "daily_expenses": round(daily_expenses_sum, 2)
    }


def _get_debt_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> Dict[str, Any]:
    """Récupère les statistiques des dettes pour une branche spécifique"""
    
    debts = db.query(Debt).filter(
        Debt.tenant_id == tenant_id,
        Debt.branch_id == branch_id,
        Debt.is_active == True
    ).all()
    
    total_debts = sum(safe_decimal_to_float(d.remaining_amount) for d in debts)
    unpaid_debts = sum(safe_decimal_to_float(d.remaining_amount) for d in debts if d.status == "unpaid")
    
    recovery_rate = 0
    total_initial = sum(safe_decimal_to_float(d.initial_amount) for d in debts)
    if total_initial > 0:
        recovery_rate = round(((total_initial - total_debts) / total_initial) * 100, 2)
    
    # Dettes du mois
    first_day_of_month = date.today().replace(day=1)
    monthly_debts = sum(
        safe_decimal_to_float(d.initial_amount)
        for d in debts
        if d.created_at and d.created_at.date() >= first_day_of_month
    )
    
    return {
        "total_debts": round(total_debts, 2),
        "unpaid_debts": round(unpaid_debts, 2),
        "recovery_rate": recovery_rate,
        "monthly_debts": round(monthly_debts, 2)
    }


def _get_purchase_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Récupère les statistiques des achats pour une branche spécifique"""
    
    purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.branch_id == branch_id,
        Purchase.status == "completed",
        Purchase.created_at >= start_dt,
        Purchase.created_at <= end_dt
    ).all()
    
    monthly_purchases = sum(safe_decimal_to_float(p.total_amount) for p in purchases)
    
    # Achats du jour
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    daily_purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.branch_id == branch_id,
        Purchase.status == "completed",
        Purchase.created_at >= today_start,
        Purchase.created_at <= today_end
    ).all()
    
    daily_purchases_sum = sum(safe_decimal_to_float(p.total_amount) for p in daily_purchases)
    
    # Nombre de fournisseurs
    suppliers_count = db.query(func.count(func.distinct(Purchase.supplier_id))).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.branch_id == branch_id,
        Purchase.status == "completed"
    ).scalar() or 0
    
    # Commandes en attente
    pending_orders = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.branch_id == branch_id,
        Purchase.status.in_(["pending", "ordered"])
    ).count()
    
    return {
        "monthly_purchases": round(monthly_purchases, 2),
        "daily_purchases": round(daily_purchases_sum, 2),
        "suppliers_count": suppliers_count,
        "pending_orders": pending_orders
    }


def _get_return_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime
) -> Dict[str, Any]:
    """Récupère les statistiques des retours pour une branche spécifique"""
    
    # Retours du mois
    monthly_returns_obj = db.query(Return).filter(
        Return.tenant_id == tenant_id,
        Return.branch_id == branch_id,
        Return.created_at >= start_dt,
        Return.created_at <= end_dt,
        Return.is_active == True
    ).all()
    
    monthly_returns = sum(safe_decimal_to_float(r.total_amount) for r in monthly_returns_obj)
    
    # Retours en attente
    pending_returns = db.query(Return).filter(
        Return.tenant_id == tenant_id,
        Return.branch_id == branch_id,
        Return.status == ReturnStatus.PENDING,
        Return.is_active == True
    ).count()
    
    # Valeur totale des retours
    total_returns_value = db.query(Return).filter(
        Return.tenant_id == tenant_id,
        Return.branch_id == branch_id,
        Return.is_active == True
    ).all()
    
    total_returns_sum = sum(safe_decimal_to_float(r.total_amount) for r in total_returns_value)
    
    return {
        "monthly_returns": round(monthly_returns, 2),
        "pending_returns": pending_returns,
        "total_returns_value": round(total_returns_sum, 2)
    }


def _get_transfer_stats_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> Dict[str, Any]:
    """Récupère les statistiques des transferts pour une branche spécifique"""
    
    # Transferts en attente (source ou destination)
    pending_transfers = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == tenant_id,
        or_(
            ProductTransfer.source_branch_id == branch_id,
            ProductTransfer.destination_branch_id == branch_id
        ),
        ProductTransfer.status == TransferStatus.PENDING
    ).count()
    
    # Transferts en transit
    in_transit_transfers = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == tenant_id,
        or_(
            ProductTransfer.source_branch_id == branch_id,
            ProductTransfer.destination_branch_id == branch_id
        ),
        ProductTransfer.status == TransferStatus.IN_TRANSIT
    ).count()
    
    return {
        "pending_transfers": pending_transfers,
        "in_transit_transfers": in_transit_transfers
    }


def _get_recent_transactions_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Récupère les transactions récentes pour une branche"""
    
    sales = db.query(Sale).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed"
    ).order_by(desc(Sale.created_at)).limit(limit).all()
    
    return [
        {
            "reference": s.reference,
            "amount": safe_decimal_to_float(s.total_amount),
            "date": s.created_at.isoformat(),
            "payment_method": s.payment_method
        }
        for s in sales
    ]


def _get_low_stock_products_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Récupère les produits en stock bas pour une branche"""
    
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True,
        Product.quantity > 0,
        Product.quantity <= Product.alert_threshold
    ).order_by(Product.quantity.asc()).limit(limit).all()
    
    return [
        {
            "name": p.name,
            "current_stock": p.quantity,
            "threshold": p.alert_threshold
        }
        for p in products
    ]


def _get_expiring_products_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Récupère les produits expirant bientôt pour une branche"""
    
    today = date.today()
    threshold = today + timedelta(days=30)
    
    products = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.branch_id == branch_id,
        Product.is_active == True,
        Product.expiry_date.isnot(None),
        Product.expiry_date <= threshold,
        Product.quantity > 0
    ).order_by(Product.expiry_date.asc()).limit(limit).all()
    
    return [
        {
            "name": p.name,
            "expiry_date": p.expiry_date.isoformat(),
            "quantity": p.quantity
        }
        for p in products
    ]


def _get_recent_debts_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Récupère les dettes récentes pour une branche"""
    
    debts = db.query(Debt).filter(
        Debt.tenant_id == tenant_id,
        Debt.branch_id == branch_id,
        Debt.is_active == True
    ).order_by(desc(Debt.created_at)).limit(limit).all()
    
    return [
        {
            "customer_name": d.customer.name if d.customer else None,
            "amount": safe_decimal_to_float(d.remaining_amount),
            "due_date": d.due_date.isoformat() if d.due_date else None
        }
        for d in debts
    ]


def _get_recent_purchases_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Récupère les achats récents pour une branche"""
    
    purchases = db.query(Purchase).filter(
        Purchase.tenant_id == tenant_id,
        Purchase.branch_id == branch_id,
        Purchase.status == "completed"
    ).order_by(desc(Purchase.created_at)).limit(limit).all()
    
    return [
        {
            "supplier_name": p.supplier.name if p.supplier else p.supplier_name,
            "amount": safe_decimal_to_float(p.total_amount),
            "date": p.created_at.isoformat()
        }
        for p in purchases
    ]


def _get_expense_categories_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """Récupère les dépenses par catégorie pour une branche"""
    
    results = db.query(
        Expense.expense_type,
        func.coalesce(func.sum(Expense.amount), 0).label("total")
    ).filter(
        Expense.tenant_id == tenant_id,
        Expense.branch_id == branch_id,
        Expense.approval_status == "approved",
        Expense.expense_date >= start_dt.date(),
        Expense.expense_date <= end_dt.date()
    ).group_by(Expense.expense_type).order_by(desc("total")).limit(limit).all()
    
    return [
        {
            "name": r.expense_type or "Autres",
            "amount": safe_decimal_to_float(r.total)
        }
        for r in results
    ]


def _get_active_users_count_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> int:
    """Récupère le nombre d'utilisateurs actifs pour une branche"""
    
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    
    count = db.query(func.count(func.distinct(Sale.created_by))).filter(
        Sale.tenant_id == tenant_id,
        Sale.branch_id == branch_id,
        Sale.status == "completed",
        Sale.created_at >= thirty_days_ago
    ).scalar() or 0
    
    return count


def _get_customers_count_by_branch(
    db: Session,
    tenant_id: Optional[UUID],
    branch_id: UUID
) -> int:
    """Récupère le nombre total de clients pour une branche"""
    
    return db.query(Customer).filter(
        Customer.tenant_id == tenant_id,
        Customer.branch_id == branch_id,
        Customer.is_active == True
    ).count()


# Endpoint de test
@router.get("/test", include_in_schema=False)
async def test_dashboard(
    current_user: User = Depends(get_current_active_user)
):
    """Endpoint de test pour le module dashboard"""
    return {
        "message": "Module Dashboard opérationnel",
        "version": "2.0.0",
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "role": current_user.role
        },
        "features": [
            "Statistiques basées sur la branche",
            "Alertes stock et péremption",
            "Historique valeur stock",
            "Historique ventes",
            "Historique bénéfices",
            "Filtres par succursale",
            "Périodes personnalisées"
        ]
    }