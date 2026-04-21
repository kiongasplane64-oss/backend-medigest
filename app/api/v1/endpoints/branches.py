from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import datetime

from app.api.deps import get_db, get_current_user, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.models.branch import Branch
from app.models.pharmacy import Pharmacy
from app.schemas.branch import BranchResponse, BranchCreate, BranchUpdate, BranchListResponse
from app.utils.config_resolver import ConfigResolver

router = APIRouter(tags=["Branches"])  


@router.get("/", response_model=BranchListResponse)
async def list_branches(
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=100),
    is_active: Optional[bool] = Query(True),
    search: Optional[str] = Query(None),
    pharmacy_id: Optional[UUID] = Query(None),
    sort_by: str = Query("created_at", pattern="^(created_at|name|code|city)$"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """Liste toutes les branches accessibles"""
    
    # Construire la requête
    query = db.query(Branch)
    
    # Correction: Si tenant existe, filtrer par tenant
    if current_tenant:
        query = query.filter(Branch.tenant_id == current_tenant.id)
    elif current_user and current_user.role == "super_admin":
        # Super admin: voir toutes les branches
        pass
    else:
        # Si pas de tenant et pas super admin, retourner vide
        return BranchListResponse(
            items=[],
            total=0,
            page=page,
            size=limit,
            pages=0
        )
    
    if pharmacy_id:
        query = query.filter(Branch.parent_pharmacy_id == pharmacy_id)
    
    if is_active is not None:
        query = query.filter(Branch.is_active == is_active)
    
    if search:
        query = query.filter(
            Branch.name.ilike(f"%{search}%") | 
            Branch.code.ilike(f"%{search}%") |
            Branch.city.ilike(f"%{search}%")
        )
    
    # Tri
    order_column = getattr(Branch, sort_by, Branch.created_at)
    if sort_order == "desc":
        query = query.order_by(order_column.desc())
    else:
        query = query.order_by(order_column.asc())
    
    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    branches = query.offset(offset).limit(limit).all()
    
    # ✅ Corriger les valeurs NULL avant la sérialisation
    for branch in branches:
        if branch.is_main_branch is None:
            branch.is_main_branch = False
        if branch.is_active is None:
            branch.is_active = True
    
    return BranchListResponse(
        items=branches,
        total=total,
        page=page,
        size=limit,
        pages=(total + limit - 1) // limit
    )


@router.get("/{branch_id}", response_model=BranchResponse)
async def get_branch(
    branch_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """Récupère une branche spécifique"""
    query = db.query(Branch).filter(Branch.id == branch_id)
    
    # Correction: Filtrer par tenant seulement si présent
    if current_tenant:
        query = query.filter(Branch.tenant_id == current_tenant.id)
    elif current_user and current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès non autorisé"
        )
    
    branch = query.first()
    
    if not branch:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Branche non trouvée"
        )
    
    # ✅ Corriger les valeurs NULL
    if branch.is_main_branch is None:
        branch.is_main_branch = False
    if branch.is_active is None:
        branch.is_active = True
    
    return branch


@router.get("/{branch_id}/statistics")
async def get_branch_statistics(
    branch_id: UUID,
    period: str = Query("month", pattern="^(day|week|month|year)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Optional[Tenant] = Depends(get_current_tenant)
):
    """Statistiques d'une branche"""
    query = db.query(Branch).filter(Branch.id == branch_id)
    
    if current_tenant:
        query = query.filter(Branch.tenant_id == current_tenant.id)
    elif current_user and current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Accès non autorisé")
    
    branch = query.first()
    
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    # Calculer les statistiques...
    from app.models.product import Product
    from app.models.sale import Sale
    
    products_count = db.query(Product).filter(
        Product.branch_id == branch_id,
        Product.is_active == True
    ).count()
    
    low_stock = db.query(Product).filter(
        Product.branch_id == branch_id,
        Product.quantity <= Product.alert_threshold,
        Product.quantity > 0
    ).count()
    
    out_of_stock = db.query(Product).filter(
        Product.branch_id == branch_id,
        Product.quantity == 0
    ).count()
    
    return {
        "branch_id": str(branch.id),
        "branch_name": branch.name,
        "products_total": products_count,
        "products_low_stock": low_stock,
        "products_out_of_stock": out_of_stock,
        "period": period
    }

@router.get("/{branch_id}/resolved-config")
def get_branch_resolved_config(
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère la configuration résolue pour une branche
    (fusionne branche + pharmacie + defaults)
    """
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == branch.parent_pharmacy_id).first()
    
    resolved_config = {
        "working_hours": ConfigResolver.resolve_working_hours(branch, pharmacy),
        "currencies": ConfigResolver.resolve_currencies(branch, pharmacy),
        "low_stock_threshold": ConfigResolver.resolve_config(
            branch, pharmacy, "lowStockThreshold", 10
        ),
        "expiry_warning_days": ConfigResolver.resolve_config(
            branch, pharmacy, "expiryWarningDays", 90
        ),
        "allow_negative_stock": ConfigResolver.resolve_config(
            branch, pharmacy, "allowNegativeStock", False
        ),
        "tax_rate": ConfigResolver.resolve_config(
            branch, pharmacy, "taxRate", 16
        ),
        "sales_type": ConfigResolver.resolve_config(
            branch, pharmacy, "salesType", "both"
        ),
        "subscription_features": ConfigResolver.get_active_subscription_features(branch)
    }
    
    return resolved_config


@router.patch("/{branch_id}/operational-config")
def update_branch_operational_config(
    branch_id: str,
    config_update: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Met à jour la configuration opérationnelle d'une branche
    (surcharge la configuration de la pharmacie)
    """
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    # Mettre à jour la config opérationnelle
    if not branch.operational_config:
        branch.operational_config = {}
    
    for key, value in config_update.items():
        branch.operational_config[key] = value
    
    branch.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "message": "Configuration opérationnelle mise à jour",
        "operational_config": branch.operational_config
    }


@router.delete("/{branch_id}/operational-config/{key}")
def reset_branch_operational_config(
    branch_id: str,
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Réinitialise une clé de configuration opérationnelle
    (revient à la valeur de la pharmacie)
    """
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    if branch.operational_config and key in branch.operational_config:
        del branch.operational_config[key]
        branch.updated_at = datetime.utcnow()
        db.commit()
    
    return {"message": f"Configuration '{key}' réinitialisée"}


@router.get("/{branch_id}/subscription/features")
def get_branch_features(
    branch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère les fonctionnalités disponibles pour une branche selon son abonnement
    """
    branch = db.query(Branch).filter(
        Branch.id == branch_id,
        Branch.tenant_id == current_tenant.id
    ).first()
    
    if not branch:
        raise HTTPException(status_code=404, detail="Branche non trouvée")
    
    features = ConfigResolver.get_active_subscription_features(branch)
    
    # Ajouter les limites actuelles
    from app.models.user import User
    from app.models.product import Product
    from app.models.sale import Sale
    
    current_users = db.query(User).filter(User.branch_id == branch.id, User.actif == True).count()
    current_products = db.query(Product).filter(Product.branch_id == branch.id).count()
    
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_transactions = db.query(Sale).filter(
        Sale.branch_id == branch.id,
        Sale.created_at >= month_start
    ).count()
    
    return {
        "branch_id": str(branch.id),
        "branch_name": branch.name,
        "subscription_status": branch.subscription_status,
        "features": features,
        "usage": {
            "current_users": current_users,
            "max_users": features.get("max_users", float('inf')),
            "current_products": current_products,
            "max_products": features.get("max_products", float('inf')),
            "current_transactions_this_month": current_transactions,
            "max_transactions_per_month": features.get("max_transactions_per_month", float('inf'))
        },
        "can_add_user": current_users < features.get("max_users", float('inf')),
        "can_add_product": current_products < features.get("max_products", float('inf'))
    }
