# app/services/expense_service.py (version complète)
from typing import Optional, List, Dict, Any, Tuple
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session, joinedload

from app.models.finance import Expense
from app.models.branch import Branch
from app.models.user import User
from app.schemas.expense import ExpenseCreate, ExpenseUpdate, ExpenseFilters

class ExpenseService:
    """Service complet pour la gestion des dépenses"""
    
    @staticmethod
    def create_expense(
        db: Session,
        tenant_id: UUID,
        user_id: UUID,
        expense_data: ExpenseCreate
    ) -> Expense:
        """Crée une nouvelle dépense"""
        # Vérifier que la branche existe si fournie
        if expense_data.branch_id:
            branch = db.query(Branch).filter(
                Branch.id == expense_data.branch_id,
                Branch.tenant_id == tenant_id,
                Branch.is_active == True
            ).first()
            if not branch:
                raise ValueError("Branche non trouvée ou inactive")
        
        # Créer la dépense
        expense = Expense(
            tenant_id=tenant_id,
            user_id=user_id,
            **expense_data.model_dump(exclude_unset=True)
        )
        
        # Calculer le total si nécessaire
        if expense.total_amount is None:
            expense.total_amount = expense.amount + expense.tax_amount
        
        db.add(expense)
        db.commit()
        db.refresh(expense)
        
        return expense
    
    @staticmethod
    def get_expense(
        db: Session,
        expense_id: UUID,
        tenant_id: UUID
    ) -> Optional[Expense]:
        """Récupère une dépense par son ID"""
        return db.query(Expense).filter(
            Expense.id == expense_id,
            Expense.tenant_id == tenant_id
        ).first()
    
    @staticmethod
    def get_expenses(
        db: Session,
        tenant_id: UUID,
        filters: ExpenseFilters,
        page: int = 1,
        per_page: int = 20,
        sort_by: str = "expense_date",
        sort_desc: bool = True
    ) -> Tuple[List[Expense], int]:
        """Récupère la liste des dépenses avec filtres et pagination"""
        query = db.query(Expense).filter(Expense.tenant_id == tenant_id)
        
        # Application des filtres
        if filters.branch_id:
            query = query.filter(Expense.branch_id == filters.branch_id)
        
        if filters.user_id:
            query = query.filter(Expense.user_id == filters.user_id)
        
        if filters.expense_type:
            query = query.filter(Expense.expense_type == filters.expense_type)
        
        if filters.approval_status:
            query = query.filter(Expense.approval_status == filters.approval_status)
        
        if filters.start_date:
            query = query.filter(Expense.expense_date >= filters.start_date)
        
        if filters.end_date:
            query = query.filter(Expense.expense_date <= filters.end_date)
        
        if filters.min_amount:
            query = query.filter(Expense.total_amount >= filters.min_amount)
        
        if filters.max_amount:
            query = query.filter(Expense.total_amount <= filters.max_amount)
        
        if filters.search:
            search_term = f"%{filters.search}%"
            query = query.filter(
                or_(
                    Expense.description.ilike(search_term),
                    Expense.supplier.ilike(search_term),
                    Expense.invoice_number.ilike(search_term),
                    Expense.payee.ilike(search_term)
                )
            )
        
        # Compter le total
        total = query.count()
        
        # Trier
        sort_column = getattr(Expense, sort_by, Expense.expense_date)
        if sort_desc:
            query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(sort_column.asc())
        
        # Pagination
        offset = (page - 1) * per_page
        expenses = query.offset(offset).limit(per_page).all()
        
        return expenses, total
    
    @staticmethod
    def update_expense(
        db: Session,
        expense_id: UUID,
        tenant_id: UUID,
        expense_data: ExpenseUpdate
    ) -> Optional[Expense]:
        """Met à jour une dépense"""
        expense = ExpenseService.get_expense(db, expense_id, tenant_id)
        if not expense:
            return None
        
        # Ne pas modifier si déjà approuvée
        if expense.approval_status == "approved":
            raise ValueError("Impossible de modifier une dépense déjà approuvée")
        
        # Mettre à jour les champs
        update_data = expense_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(expense, field, value)
        
        # Recalculer le total si nécessaire
        if 'amount' in update_data or 'tax_amount' in update_data:
            expense.total_amount = expense.amount + expense.tax_amount
        
        db.commit()
        db.refresh(expense)
        
        return expense
    
    @staticmethod
    def delete_expense(
        db: Session,
        expense_id: UUID,
        tenant_id: UUID
    ) -> bool:
        """Supprime une dépense"""
        expense = ExpenseService.get_expense(db, expense_id, tenant_id)
        if not expense:
            return False
        
        # Ne pas supprimer si déjà approuvée
        if expense.approval_status == "approved":
            raise ValueError("Impossible de supprimer une dépense déjà approuvée")
        
        db.delete(expense)
        db.commit()
        
        return True
    
    @staticmethod
    def approve_expense(
        db: Session,
        expense_id: UUID,
        tenant_id: UUID,
        approver_id: UUID,
        approved: bool,
        rejection_reason: Optional[str] = None
    ) -> Optional[Expense]:
        """Approuve ou rejette une dépense"""
        expense = ExpenseService.get_expense(db, expense_id, tenant_id)
        if not expense:
            return None
        
        if expense.approval_status != "pending":
            raise ValueError(f"La dépense est déjà {expense.approval_status}")
        
        if approved:
            expense.approval_status = "approved"
            expense.rejection_reason = None
        else:
            if not rejection_reason:
                raise ValueError("Une raison est requise pour rejeter la dépense")
            expense.approval_status = "rejected"
            expense.rejection_reason = rejection_reason
        
        expense.approved_by = approver_id
        
        db.commit()
        db.refresh(expense)
        
        return expense
    
    @staticmethod
    def get_expenses_by_branch(
        db: Session,
        tenant_id: UUID,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Récupère les dépenses regroupées par branche"""
        results = db.query(
            Branch.id.label('branch_id'),
            Branch.name.label('branch_name'),
            func.sum(Expense.total_amount).label('total_expenses'),
            func.count(Expense.id).label('expense_count'),
            func.avg(Expense.amount).label('average_expense')
        ).outerjoin(
            Expense, and_(
                Expense.branch_id == Branch.id,
                Expense.tenant_id == tenant_id,
                Expense.expense_date.between(start_date, end_date)
            )
        ).filter(
            Branch.tenant_id == tenant_id,
            Branch.is_active == True
        ).group_by(
            Branch.id, Branch.name
        ).all()
        
        # Calculer le total général pour les pourcentages
        total_all = sum(float(r.total_expenses or 0) for r in results)
        
        return [
            {
                "branch_id": str(r.branch_id),
                "branch_name": r.branch_name,
                "total_expenses": float(r.total_expenses or 0),
                "expense_count": r.expense_count or 0,
                "average_expense": float(r.average_expense or 0),
                "percentage_of_total": (float(r.total_expenses or 0) / total_all * 100) if total_all > 0 else 0
            }
            for r in results
        ]
    
    @staticmethod
    def get_expenses_by_user(
        db: Session,
        tenant_id: UUID,
        start_date: date,
        end_date: date
    ) -> List[Dict[str, Any]]:
        """Récupère les dépenses regroupées par utilisateur"""
        results = db.query(
            User.id.label('user_id'),
            User.nom_complet.label('username'),
            User.email,
            func.sum(Expense.total_amount).label('total_expenses'),
            func.count(Expense.id).label('expense_count'),
            func.avg(Expense.amount).label('average_expense')
        ).outerjoin(
            Expense, and_(
                Expense.user_id == User.id,
                Expense.tenant_id == tenant_id,
                Expense.expense_date.between(start_date, end_date)
            )
        ).filter(
            User.tenant_id == tenant_id,
            User.actif == True
        ).group_by(
            User.id, User.nom_complet, User.email
        ).all()
        
        # Calculer le total général
        total_all = sum(float(r.total_expenses or 0) for r in results)
        
        return [
            {
                "user_id": str(r.user_id),
                "username": r.username,
                "email": r.email,
                "total_expenses": float(r.total_expenses or 0),
                "expense_count": r.expense_count or 0,
                "average_expense": float(r.average_expense or 0),
                "percentage_of_total": (float(r.total_expenses or 0) / total_all * 100) if total_all > 0 else 0
            }
            for r in results
        ]
    
    @staticmethod
    def get_summary(
        db: Session,
        tenant_id: UUID,
        start_date: date,
        end_date: date
    ) -> Dict[str, Any]:
        """Récupère un résumé des dépenses"""
        # Statistiques générales
        stats = db.query(
            func.sum(Expense.total_amount).label('total'),
            func.count(Expense.id).label('count'),
            func.avg(Expense.total_amount).label('avg')
        ).filter(
            Expense.tenant_id == tenant_id,
            Expense.expense_date.between(start_date, end_date)
        ).first()
        
        # Par catégorie
        by_category = db.query(
            Expense.expense_type,
            func.sum(Expense.total_amount).label('total'),
            func.count(Expense.id).label('count')
        ).filter(
            Expense.tenant_id == tenant_id,
            Expense.expense_date.between(start_date, end_date)
        ).group_by(Expense.expense_type).all()
        
        # Par statut d'approbation
        by_status = db.query(
            Expense.approval_status,
            func.sum(Expense.total_amount).label('total'),
            func.count(Expense.id).label('count')
        ).filter(
            Expense.tenant_id == tenant_id,
            Expense.expense_date.between(start_date, end_date)
        ).group_by(Expense.approval_status).all()
        
        return {
            "total_expenses": float(stats.total or 0),
            "total_count": stats.count or 0,
            "average_expense": float(stats.avg or 0),
            "by_category": {
                row.expense_type: {
                    "total": float(row.total),
                    "count": row.count
                } for row in by_category
            },
            "by_status": {
                row.approval_status: {
                    "total": float(row.total),
                    "count": row.count
                } for row in by_status
            },
            "period_start": start_date,
            "period_end": end_date
        }
    
    @staticmethod
    def get_expense_with_details(
        db: Session,
        expense_id: UUID,
        tenant_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Récupère une dépense avec les détails des relations"""
        result = db.query(
            Expense,
            Branch.name.label('branch_name'),
            User.nom_complet.label('user_name'),
            User.email.label('user_email'),
            db.query(User.nom_complet).filter(User.id == Expense.approved_by).label('approver_name')
        ).outerjoin(
            Branch, Expense.branch_id == Branch.id
        ).outerjoin(
            User, Expense.user_id == User.id
        ).filter(
            Expense.id == expense_id,
            Expense.tenant_id == tenant_id
        ).first()
        
        if not result:
            return None
        
        expense, branch_name, user_name, user_email, approver_name = result
        
        return {
            "id": str(expense.id),
            "tenant_id": str(expense.tenant_id),
            "branch_id": str(expense.branch_id) if expense.branch_id else None,
            "branch_name": branch_name,
            "user_id": str(expense.user_id) if expense.user_id else None,
            "user_name": user_name,
            "user_email": user_email,
            "expense_date": expense.expense_date.isoformat(),
            "expense_type": expense.expense_type,
            "amount": float(expense.amount),
            "tax_amount": float(expense.tax_amount),
            "total_amount": float(expense.total_amount),
            "supplier": expense.supplier,
            "payee": expense.payee,
            "payment_method": expense.payment_method,
            "payment_reference": expense.payment_reference,
            "description": expense.description,
            "notes": expense.notes,
            "invoice_number": expense.invoice_number,
            "invoice_date": expense.invoice_date.isoformat() if expense.invoice_date else None,
            "is_recurring": expense.is_recurring,
            "recurrence_interval": expense.recurrence_interval,
            "next_due_date": expense.next_due_date.isoformat() if expense.next_due_date else None,
            "approval_status": expense.approval_status,
            "approved_by": str(expense.approved_by) if expense.approved_by else None,
            "approver_name": approver_name,
            "rejection_reason": expense.rejection_reason,
            "cost_center": expense.cost_center,
            "project_code": expense.project_code,
            "created_at": expense.created_at.isoformat(),
            "updated_at": expense.updated_at.isoformat()
        }