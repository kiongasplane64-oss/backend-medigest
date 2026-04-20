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