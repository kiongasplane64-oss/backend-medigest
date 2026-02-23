# app/api/routes/cost.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, extract
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
import pandas as pd
from io import BytesIO

from app.db.session import get_db
from app.models.cost import Cost, CostAllocation, Budget, Supplier
from app.models.department import Department
from app.models.project import Project
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.cost import (
    CostCreate, CostInDB, CostUpdate,
    CostAllocationCreate, BudgetCreate, BudgetInDB,
    SupplierCreate, SupplierInDB,
    CostSummary, CostAnalytics
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.cost import CostService

router = APIRouter(prefix="/costs", tags=["Costs"])
logger = logging.getLogger(__name__)

@router.post("/", response_model=CostInDB)
@require_permission("costs_manage")
def create_cost(
    cost_data: CostCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau coût
    """
    try:
        # Calculer le montant total
        total_amount = cost_data.amount + cost_data.tax_amount
        
        # Créer le coût
        cost = Cost(
            tenant_id=current_tenant.id,
            category=cost_data.category.value,
            subcategory=cost_data.subcategory,
            amount=cost_data.amount,
            tax_amount=cost_data.tax_amount,
            total_amount=total_amount,
            currency=current_tenant.currency or "CDF",
            description=cost_data.description,
            payment_date=cost_data.payment_date,
            payment_method=cost_data.payment_method.value,
            is_paid=cost_data.is_paid,
            invoice_number=cost_data.invoice_number,
            supplier_id=cost_data.supplier_id,
            is_recurring=cost_data.is_recurring,
            frequency=cost_data.frequency.value,
            recurring_until=cost_data.recurring_until,
            budget_id=cost_data.budget_id,
            notes=cost_data.notes,
            tags=cost_data.tags,
            created_by=current_user.id,
            approved_by=current_user.id if cost_data.is_paid else None
        )
        
        db.add(cost)
        db.flush()
        
        # Si récurrent, planifier le prochain paiement
        if cost_data.is_recurring and cost_data.frequency != "one_time":
            next_date = calculate_next_payment_date(
                cost_data.payment_date,
                cost_data.frequency
            )
            cost.next_payment_date = next_date
        
        # Mettre à jour le budget si spécifié
        if cost_data.budget_id:
            budget = db.query(Budget).filter(
                Budget.id == cost_data.budget_id,
                Budget.tenant_id == current_tenant.id
            ).first()
            
            if budget:
                budget.update_spent_amount()
        
        db.commit()
        db.refresh(cost)
        
        logger.info(f"Coût créé: {cost.description} - {total_amount} {cost.currency}")
        
        return cost
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du coût: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du coût"
        )

@router.get("/", response_model=List[CostInDB])
@require_permission("costs_view")
def list_costs(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    category: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    supplier_id: Optional[UUID] = None,
    budget_id: Optional[UUID] = None,
    is_paid: Optional[bool] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    search: Optional[str] = None
):
    """
    Liste les coûts avec filtres
    """
    query = db.query(Cost).filter(Cost.tenant_id == current_tenant.id)
    
    # Appliquer les filtres
    if category:
        query = query.filter(Cost.category == category)
    
    if start_date:
        query = query.filter(Cost.payment_date >= start_date)
    
    if end_date:
        query = query.filter(Cost.payment_date <= end_date)
    
    if supplier_id:
        query = query.filter(Cost.supplier_id == supplier_id)
    
    if budget_id:
        query = query.filter(Cost.budget_id == budget_id)
    
    if is_paid is not None:
        query = query.filter(Cost.is_paid == is_paid)
    
    if min_amount is not None:
        query = query.filter(Cost.total_amount >= min_amount)
    
    if max_amount is not None:
        query = query.filter(Cost.total_amount <= max_amount)
    
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Cost.description.ilike(search_term),
                Cost.notes.ilike(search_term),
                Cost.invoice_number.ilike(search_term)
            )
        )
    
    # Trier par date de paiement décroissante
    costs = query.order_by(
        Cost.payment_date.desc(),
        Cost.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return costs

@router.get("/summary", response_model=CostSummary)
@require_permission("costs_view")
def get_cost_summary(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    period: str = Query("month", pattern="^(day|week|month|quarter|year|all)$"),
    year: Optional[int] = None,
    month: Optional[int] = None
):
    """
    Obtient un résumé des coûts
    """
    # Déterminer la période
    today = date.today()
    start_date, end_date = get_period_dates(period, year, month)
    
    # Requête de base
    query = db.query(Cost).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    )
    
    # Total des coûts
    total_costs = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).scalar() or 0.0
    
    # Par catégorie
    category_query = db.query(
        Cost.category,
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).group_by(Cost.category)
    
    by_category = {
        category: total for category, total in category_query.all()
    }
    
    # Par mois
    monthly_query = db.query(
        extract('year', Cost.payment_date).label('year'),
        extract('month', Cost.payment_date).label('month'),
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date - timedelta(days=365),  # Dernier an
        Cost.payment_date <= end_date
    ).group_by('year', 'month').order_by('year', 'month')
    
    by_month = {}
    for year_val, month_val, total in monthly_query.all():
        key = f"{int(year_val)}-{int(month_val):02d}"
        by_month[key] = total
    
    # Coûts les plus élevés
    top_costs = db.query(Cost).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).order_by(
        Cost.total_amount.desc()
    ).limit(10).all()
    
    # Formatage des coûts les plus élevés
    formatted_top_costs = []
    for cost in top_costs:
        formatted_top_costs.append({
            "id": cost.id,
            "description": cost.description,
            "category": cost.category,
            "amount": cost.total_amount,
            "date": cost.payment_date,
            "supplier": cost.supplier.name if cost.supplier else None
        })
    
    # Variance avec le budget
    budget_variance = {}
    budgets = db.query(Budget).filter(
        Budget.tenant_id == current_tenant.id,
        Budget.start_date <= end_date,
        Budget.end_date >= start_date,
        Budget.is_active == True
    ).all()
    
    for budget in budgets:
        budget_variance[budget.name] = {
            "allocated": budget.allocated_amount,
            "spent": budget.spent_amount,
            "remaining": budget.remaining_amount,
            "percentage": (budget.spent_amount / budget.allocated_amount * 100) if budget.allocated_amount > 0 else 0
        }
    
    # Calcul de la moyenne mensuelle
    month_count = max(1, (end_date - start_date).days / 30)
    average_monthly = total_costs / month_count
    
    return CostSummary(
        period=period,
        total_costs=total_costs,
        by_category=by_category,
        by_month=by_month,
        average_monthly=average_monthly,
        top_costs=formatted_top_costs,
        budget_variance=budget_variance
    )

@router.post("/import")
@require_permission("costs_manage")
def import_costs(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Importe des coûts depuis un fichier Excel
    """
    if not file.filename.endswith(('.xlsx', '.xls', '.csv')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format de fichier non supporté. Utilisez Excel ou CSV."
        )
    
    try:
        # Lire le fichier
        contents = file.file.read()
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(BytesIO(contents))
        else:
            df = pd.read_excel(BytesIO(contents))
        
        # Valider les colonnes requises
        required_columns = ['category', 'amount', 'description', 'payment_date']
        missing_columns = [col for col in required_columns if col not in df.columns]
        
        if missing_columns:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Colonnes manquantes: {', '.join(missing_columns)}"
            )
        
        imported_count = 0
        errors = []
        
        for index, row in df.iterrows():
            try:
                # Convertir la date
                payment_date = pd.to_datetime(row['payment_date']).date()
                
                # Créer le coût
                cost = Cost(
                    tenant_id=current_tenant.id,
                    category=row['category'],
                    amount=float(row['amount']),
                    tax_amount=float(row.get('tax_amount', 0)),
                    total_amount=float(row['amount']) + float(row.get('tax_amount', 0)),
                    currency=row.get('currency', 'CDF'),
                    description=row['description'],
                    payment_date=payment_date,
                    payment_method=row.get('payment_method', 'cash'),
                    is_paid=bool(row.get('is_paid', True)),
                    invoice_number=row.get('invoice_number'),
                    notes=row.get('notes'),
                    tags=row.get('tags', '').split(',') if pd.notna(row.get('tags')) else [],
                    created_by=current_user.id,
                    approved_by=current_user.id if bool(row.get('is_paid', True)) else None
                )
                
                # Gérer le fournisseur
                if pd.notna(row.get('supplier')):
                    supplier = db.query(Supplier).filter(
                        Supplier.tenant_id == current_tenant.id,
                        Supplier.name == row['supplier']
                    ).first()
                    
                    if not supplier:
                        supplier = Supplier(
                            tenant_id=current_tenant.id,
                            name=row['supplier'],
                            created_at=datetime.utcnow()
                        )
                        db.add(supplier)
                        db.flush()
                    
                    cost.supplier_id = supplier.id
                
                db.add(cost)
                imported_count += 1
                
                if imported_count % 100 == 0:
                    db.commit()
                    
            except Exception as e:
                errors.append(f"Ligne {index + 2}: {str(e)}")
        
        db.commit()
        
        return {
            "message": f"{imported_count} coûts importés avec succès",
            "imported_count": imported_count,
            "errors": errors if errors else None
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'importation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'importation: {str(e)}"
        )

@router.get("/export")
@require_permission("costs_view")
def export_costs(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    format: str = Query("excel", pattern="^(excel|csv|pdf)$")
):
    """
    Exporte les coûts dans différents formats
    """
    if background_tasks:
        # Lancer l'export en arrière-plan
        from app.services.export import ExportService
        export_service = ExportService(current_tenant)
        
        background_tasks.add_task(
            export_service.export_costs,
            start_date=start_date,
            end_date=end_date,
            export_format=format,
            user_id=current_user.id
        )
        
        return {
            "message": "Export démarré en arrière-plan",
            "format": format,
            "start_date": start_date,
            "end_date": end_date
        }
    
    # Retour direct pour petits exports
    return {"message": "Export synchrone non implémenté"}

@router.post("/budgets", response_model=BudgetInDB)
@require_permission("costs_manage")
def create_budget(
    budget_data: BudgetCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau budget
    """
    try:
        # Vérifier les chevauchements
        overlapping_budgets = db.query(Budget).filter(
            Budget.tenant_id == current_tenant.id,
            Budget.category == budget_data.category.value,
            Budget.is_active == True,
            or_(
                and_(
                    Budget.start_date <= budget_data.end_date,
                    Budget.end_date >= budget_data.start_date
                )
            )
        ).all()
        
        if overlapping_budgets:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un budget actif existe déjà pour cette période et catégorie"
            )
        
        # Créer le budget
        budget = Budget(
            tenant_id=current_tenant.id,
            name=budget_data.name,
            description=budget_data.description,
            category=budget_data.category.value,
            period_type=budget_data.period_type.value,
            start_date=budget_data.start_date,
            end_date=budget_data.end_date,
            allocated_amount=budget_data.allocated_amount,
            warning_threshold=budget_data.warning_threshold,
            critical_threshold=budget_data.critical_threshold,
            remaining_amount=budget_data.allocated_amount,
            owner_id=current_user.id
        )
        
        db.add(budget)
        db.commit()
        db.refresh(budget)
        
        logger.info(f"Budget créé: {budget.name} - {budget.allocated_amount}")
        
        return budget
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du budget: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du budget"
        )

@router.get("/budgets", response_model=List[BudgetInDB])
@require_permission("costs_view")
def list_budgets(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    year: Optional[int] = None
):
    """
    Liste les budgets
    """
    query = db.query(Budget).filter(Budget.tenant_id == current_tenant.id)
    
    if category:
        query = query.filter(Budget.category == category)
    
    if is_active is not None:
        query = query.filter(Budget.is_active == is_active)
    
    if year:
        query = query.filter(
            extract('year', Budget.start_date) == year
        )
    
    budgets = query.order_by(
        Budget.start_date.desc(),
        Budget.created_at.desc()
    ).all()
    
    # Mettre à jour les montants dépensés
    for budget in budgets:
        budget.update_spent_amount()
    
    return budgets

@router.get("/budgets/{budget_id}/alerts")
@require_permission("costs_view")
def get_budget_alerts(
    budget_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les alertes pour un budget
    """
    budget = db.query(Budget).filter(
        Budget.id == budget_id,
        Budget.tenant_id == current_tenant.id
    ).first()
    
    if not budget:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Budget non trouvé"
        )
    
    budget.update_spent_amount()
    
    alerts = []
    percentage = (budget.spent_amount / budget.allocated_amount * 100) if budget.allocated_amount > 0 else 0
    
    if percentage >= budget.critical_threshold:
        alerts.append({
            "level": "critical",
            "message": f"Budget dépassé à {percentage:.1f}%",
            "percentage": percentage
        })
    elif percentage >= budget.warning_threshold:
        alerts.append({
            "level": "warning",
            "message": f"Budget approche de la limite: {percentage:.1f}%",
            "percentage": percentage
        })
    
    return {
        "budget": budget,
        "alerts": alerts,
        "spent_percentage": percentage
    }

@router.post("/suppliers", response_model=SupplierInDB)
@require_permission("costs_manage")
def create_supplier(
    supplier_data: SupplierCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau fournisseur
    """
    try:
        supplier = Supplier(
            tenant_id=current_tenant.id,
            name=supplier_data.name,
            company_name=supplier_data.company_name,
            tax_id=supplier_data.tax_id,
            email=supplier_data.email,
            phone=supplier_data.phone,
            address=supplier_data.address,
            website=supplier_data.website,
            bank_name=supplier_data.bank_name,
            bank_account=supplier_data.bank_account,
            payment_terms=supplier_data.payment_terms,
            categories=supplier_data.categories
        )
        
        db.add(supplier)
        db.commit()
        db.refresh(supplier)
        
        logger.info(f"Fournisseur créé: {supplier.name}")
        
        return supplier
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du fournisseur: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du fournisseur"
        )

@router.get("/analytics", response_model=CostAnalytics)
@require_permission("costs_view")
def get_cost_analytics(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    months: int = Query(12, ge=1, le=60)
):
    """
    Obtient des analyses avancées sur les coûts
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=months * 30)
    
    # Tendances mensuelles
    monthly_query = db.query(
        extract('year', Cost.payment_date).label('year'),
        extract('month', Cost.payment_date).label('month'),
        func.sum(Cost.total_amount).label('total')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).group_by('year', 'month').order_by('year', 'month')
    
    monthly_trend = []
    for year_val, month_val, total in monthly_query.all():
        monthly_trend.append({
            "period": f"{int(year_val)}-{int(month_val):02d}",
            "amount": total,
            "year": int(year_val),
            "month": int(month_val)
        })
    
    # Distribution par catégorie
    category_query = db.query(
        Cost.category,
        func.sum(Cost.total_amount).label('total'),
        func.count(Cost.id).label('count')
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).group_by(Cost.category)
    
    category_distribution = []
    for category, total, count in category_query.all():
        category_distribution.append({
            "category": category,
            "amount": total,
            "count": count,
            "percentage": (total / sum(item['amount'] for item in category_distribution) * 100) if category_distribution else 0
        })
    
    # Analyse des fournisseurs
    supplier_query = db.query(
        Supplier.name,
        func.sum(Cost.total_amount).label('total'),
        func.count(Cost.id).label('count')
    ).join(
        Cost, Supplier.id == Cost.supplier_id
    ).filter(
        Cost.tenant_id == current_tenant.id,
        Cost.payment_date >= start_date,
        Cost.payment_date <= end_date
    ).group_by(Supplier.name).order_by(func.sum(Cost.total_amount).desc()).limit(10)
    
    supplier_analysis = []
    for name, total, count in supplier_query.all():
        supplier_analysis.append({
            "name": name,
            "amount": total,
            "count": count
        })
    
    # Analyse de variance
    total_this_year = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        extract('year', Cost.payment_date) == end_date.year
    ).scalar() or 0.0
    
    total_last_year = db.query(func.sum(Cost.total_amount)).filter(
        Cost.tenant_id == current_tenant.id,
        extract('year', Cost.payment_date) == end_date.year - 1
    ).scalar() or 0.0
    
    variance = total_this_year - total_last_year
    variance_percentage = (variance / total_last_year * 100) if total_last_year > 0 else 0
    
    # Recommandations
    recommendations = []
    
    # Recommandations basées sur les données
    if variance_percentage > 20:
        recommendations.append("Augmentation significative des coûts cette année. Examiner les dépenses.")
    
    top_category = max(category_distribution, key=lambda x: x['amount']) if category_distribution else None
    if top_category and top_category['percentage'] > 50:
        recommendations.append(f"Concentration élevée dans {top_category['category']}. Diversifier les dépenses.")
    
    return CostAnalytics(
        monthly_trend=monthly_trend,
        category_distribution=category_distribution,
        supplier_analysis=supplier_analysis,
        variance_analysis={
            "current_year": total_this_year,
            "last_year": total_last_year,
            "variance": variance,
            "variance_percentage": variance_percentage
        },
        recommendations=recommendations
    )

# ==============================================
# FONCTIONS UTILITAIRES
# ==============================================

def calculate_next_payment_date(current_date: date, frequency: str) -> date:
    """Calcule la prochaine date de paiement"""
    if frequency == "daily":
        return current_date + timedelta(days=1)
    elif frequency == "weekly":
        return current_date + timedelta(days=7)
    elif frequency == "monthly":
        # Ajouter un mois
        year = current_date.year
        month = current_date.month + 1
        if month > 12:
            month = 1
            year += 1
        return date(year, month, min(current_date.day, 28))
    elif frequency == "quarterly":
        return current_date + timedelta(days=90)
    elif frequency == "yearly":
        return date(current_date.year + 1, current_date.month, current_date.day)
    else:
        return current_date

def get_period_dates(period: str, year: Optional[int] = None, month: Optional[int] = None):
    """Retourne les dates de début et fin pour une période donnée"""
    today = date.today()
    
    if period == "day":
        start_date = today
        end_date = today
    elif period == "week":
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif period == "month":
        if year and month:
            start_date = date(year, month, 1)
            end_date = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year + 1, 1, 1) - timedelta(days=1)
        else:
            start_date = date(today.year, today.month, 1)
            end_date = date(today.year, today.month + 1, 1) - timedelta(days=1) if today.month < 12 else date(today.year + 1, 1, 1) - timedelta(days=1)
    elif period == "quarter":
        quarter = (today.month - 1) // 3
        start_month = quarter * 3 + 1
        start_date = date(today.year, start_month, 1)
        end_date = date(today.year, start_month + 3, 1) - timedelta(days=1)
    elif period == "year":
        if year:
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)
        else:
            start_date = date(today.year, 1, 1)
            end_date = date(today.year, 12, 31)
    else:  # "all"
        start_date = date(2000, 1, 1)  # Date ancienne
        end_date = date(2100, 12, 31)  # Date future
    
    return start_date, end_date
