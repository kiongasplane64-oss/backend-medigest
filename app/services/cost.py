# app/services/cost.py
from datetime import date, datetime, timedelta
from typing import List, Dict, Any
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.cost import Cost, Supplier
from app.models.department import Department
from app.models.project import Project


class CostService:
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    def generate_monthly_report(self, year: int, month: int) -> Dict[str, Any]:
        """
        Génère un rapport mensuel des coûts
        """
        start_date = date(year, month, 1)
        if month < 12:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        else:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        
        # Récupérer les données
        costs = self.db.query(Cost).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= start_date,
            Cost.payment_date <= end_date
        ).all()
        
        # Préparer le rapport
        report = {
            "period": f"{year}-{month:02d}",
            "total_costs": sum(float(c.total_amount) for c in costs),
            "total_transactions": len(costs),
            "by_category": {},
            "by_supplier": {},
            "by_department": {},
            "by_project": {},
            "budget_comparison": {}
        }
        
        # Analyser par catégorie
        for cost in costs:
            category = cost.category
            if category not in report["by_category"]:
                report["by_category"][category] = {
                    "amount": 0.0,
                    "count": 0
                }
            report["by_category"][category]["amount"] += float(cost.total_amount)
            report["by_category"][category]["count"] += 1
        
        # Analyser par fournisseur
        for cost in costs:
            if cost.supplier:
                supplier_name = cost.supplier.name
                if supplier_name not in report["by_supplier"]:
                    report["by_supplier"][supplier_name] = {
                        "amount": 0.0,
                        "count": 0
                    }
                report["by_supplier"][supplier_name]["amount"] += float(cost.total_amount)
                report["by_supplier"][supplier_name]["count"] += 1
        
        # Analyser par département
        for cost in costs:
            if cost.department:
                dept_name = cost.department.name
                if dept_name not in report["by_department"]:
                    report["by_department"][dept_name] = {
                        "amount": 0.0,
                        "count": 0
                    }
                report["by_department"][dept_name]["amount"] += float(cost.total_amount)
                report["by_department"][dept_name]["count"] += 1
        
        # Analyser par projet
        for cost in costs:
            if cost.project:
                project_name = cost.project.name
                if project_name not in report["by_project"]:
                    report["by_project"][project_name] = {
                        "amount": 0.0,
                        "count": 0
                    }
                report["by_project"][project_name]["amount"] += float(cost.total_amount)
                report["by_project"][project_name]["count"] += 1
        
        return report
    
    def predict_future_costs(self, months: int = 6) -> List[Dict[str, Any]]:
        """
        Prédit les coûts futurs basés sur l'historique
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=months * 30)
        
        # Récupérer les coûts historiques
        historical_costs = self.db.query(Cost).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= start_date,
            Cost.payment_date <= end_date
        ).all()
        
        # Analyser les tendances
        monthly_totals = {}
        for cost in historical_costs:
            key = f"{cost.payment_date.year}-{cost.payment_date.month:02d}"
            if key not in monthly_totals:
                monthly_totals[key] = 0.0
            monthly_totals[key] += float(cost.total_amount)
        
        # Prédiction simple (moyenne mobile)
        if monthly_totals:
            average_monthly = sum(monthly_totals.values()) / len(monthly_totals)
        else:
            average_monthly = 0.0
        
        # Générer les prédictions
        predictions = []
        for i in range(1, months + 1):
            prediction_date = end_date + timedelta(days=30 * i)
            predictions.append({
                "period": f"{prediction_date.year}-{prediction_date.month:02d}",
                "predicted_amount": average_monthly,
                "confidence": 0.7  # Score de confiance
            })
        
        return predictions
    
    def optimize_costs(self) -> List[Dict[str, Any]]:
        """
        Identifie les opportunités d'optimisation des coûts
        """
        recommendations = []
        
        # Analyser les fournisseurs
        suppliers = self.db.query(Supplier).filter(
            Supplier.tenant_id == self.tenant_id
        ).all()
        
        for supplier in suppliers:
            supplier_costs = self.db.query(func.sum(Cost.total_amount)).filter(
                Cost.tenant_id == self.tenant_id,
                Cost.supplier_id == supplier.id
            ).scalar() or 0.0
            
            if supplier_costs > 100000:  # Seuil arbitraire
                recommendations.append({
                    "type": "supplier_negotiation",
                    "title": f"Négocier avec {supplier.name}",
                    "description": f"Coût total: {supplier_costs:.2f}",
                    "potential_savings": supplier_costs * 0.1,  # 10% d'économie potentielle
                    "priority": "high" if supplier_costs > 500000 else "medium"
                })
        
        # Analyser les catégories de coûts
        categories = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= date.today() - timedelta(days=365)
        ).group_by(Cost.category).all()
        
        for category, total in categories:
            if total > 200000:  # Seuil arbitraire
                recommendations.append({
                    "type": "category_optimization",
                    "title": f"Optimiser les coûts de {category}",
                    "description": f"Dépenses annuelles: {float(total):.2f}",
                    "potential_savings": float(total) * 0.15,  # 15% d'économie potentielle
                    "priority": "high" if total > 1000000 else "medium"
                })
        
        # Analyser les départements
        departments = self.db.query(
            Department,
            func.sum(Cost.total_amount).label('total')
        ).join(
            Cost, Department.id == Cost.department_id
        ).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= date.today() - timedelta(days=365)
        ).group_by(Department.id).all()
        
        for department, total in departments:
            if total > 300000:  # Seuil arbitraire
                recommendations.append({
                    "type": "department_review",
                    "title": f"Révision des coûts du département {department.name}",
                    "description": f"Dépenses annuelles: {float(total):.2f}",
                    "potential_savings": float(total) * 0.05,  # 5% d'économie potentielle
                    "priority": "medium"
                })
        
        # Analyser les projets
        projects = self.db.query(
            Project,
            func.sum(Cost.total_amount).label('total')
        ).join(
            Cost, Project.id == Cost.project_id
        ).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= date.today() - timedelta(days=365)
        ).group_by(Project.id).all()
        
        for project, total in projects:
            if total > 500000:  # Seuil arbitraire
                recommendations.append({
                    "type": "project_cost_review",
                    "title": f"Révision des coûts du projet {project.name}",
                    "description": f"Dépenses annuelles: {float(total):.2f}",
                    "potential_savings": float(total) * 0.08,  # 8% d'économie potentielle
                    "priority": "high" if total > 1000000 else "medium"
                })
        
        return recommendations
    
    def get_cost_breakdown(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """
        Obtient une répartition détaillée des coûts pour une période donnée
        """
        # Récupérer les coûts de la période
        costs = self.db.query(Cost).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= start_date,
            Cost.payment_date <= end_date
        ).all()
        
        total_amount = sum(float(c.total_amount) for c in costs)
        
        breakdown = {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "total_amount": total_amount,
            "transaction_count": len(costs),
            "average_transaction": total_amount / len(costs) if costs else 0,
            "categories": {},
            "payment_methods": {},
            "monthly_trend": {}
        }
        
        # Répartition par catégorie
        for cost in costs:
            category = cost.category
            if category not in breakdown["categories"]:
                breakdown["categories"][category] = {
                    "amount": 0.0,
                    "count": 0,
                    "percentage": 0.0
                }
            breakdown["categories"][category]["amount"] += float(cost.total_amount)
            breakdown["categories"][category]["count"] += 1
        
        # Calculer les pourcentages par catégorie
        for category in breakdown["categories"]:
            cat_data = breakdown["categories"][category]
            cat_data["percentage"] = (cat_data["amount"] / total_amount * 100) if total_amount > 0 else 0
        
        # Répartition par méthode de paiement
        for cost in costs:
            method = cost.payment_method
            if method not in breakdown["payment_methods"]:
                breakdown["payment_methods"][method] = {
                    "amount": 0.0,
                    "count": 0
                }
            breakdown["payment_methods"][method]["amount"] += float(cost.total_amount)
            breakdown["payment_methods"][method]["count"] += 1
        
        # Tendance mensuelle
        for cost in costs:
            month_key = cost.payment_date.strftime("%Y-%m")
            if month_key not in breakdown["monthly_trend"]:
                breakdown["monthly_trend"][month_key] = {
                    "amount": 0.0,
                    "count": 0
                }
            breakdown["monthly_trend"][month_key]["amount"] += float(cost.total_amount)
            breakdown["monthly_trend"][month_key]["count"] += 1
        
        # Top 5 des coûts les plus élevés
        sorted_costs = sorted(costs, key=lambda x: float(x.total_amount), reverse=True)[:5]
        breakdown["top_costs"] = [
            {
                "id": str(cost.id),
                "description": cost.description,
                "amount": float(cost.total_amount),
                "category": cost.category,
                "date": cost.payment_date.isoformat(),
                "supplier": cost.supplier.name if cost.supplier else None
            }
            for cost in sorted_costs
        ]
        
        return breakdown
    
    def compare_periods(self, period1_start: date, period1_end: date, 
                       period2_start: date, period2_end: date) -> Dict[str, Any]:
        """
        Compare les coûts entre deux périodes
        """
        # Coûts de la période 1
        period1_costs = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= period1_start,
            Cost.payment_date <= period1_end
        ).scalar() or 0.0
        
        # Coûts de la période 2
        period2_costs = self.db.query(func.sum(Cost.total_amount)).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= period2_start,
            Cost.payment_date <= period2_end
        ).scalar() or 0.0
        
        # Calculer la variation
        if period1_costs > 0:
            variance = period2_costs - period1_costs
            variance_percentage = (variance / period1_costs) * 100
        else:
            variance = period2_costs
            variance_percentage = 100.0 if period2_costs > 0 else 0.0
        
        # Analyse par catégorie
        period1_by_category = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= period1_start,
            Cost.payment_date <= period1_end
        ).group_by(Cost.category).all()
        
        period2_by_category = self.db.query(
            Cost.category,
            func.sum(Cost.total_amount).label('total')
        ).filter(
            Cost.tenant_id == self.tenant_id,
            Cost.payment_date >= period2_start,
            Cost.payment_date <= period2_end
        ).group_by(Cost.category).all()
        
        category_comparison = {}
        for category, total in period1_by_category:
            category_comparison[category] = {
                "period1": float(total),
                "period2": 0.0,
                "variance": 0.0,
                "variance_percentage": 0.0
            }
        
        for category, total in period2_by_category:
            if category in category_comparison:
                category_comparison[category]["period2"] = float(total)
                period1_total = category_comparison[category]["period1"]
                if period1_total > 0:
                    variance = float(total) - period1_total
                    category_comparison[category]["variance"] = variance
                    category_comparison[category]["variance_percentage"] = (variance / period1_total) * 100
                else:
                    category_comparison[category]["variance"] = float(total)
                    category_comparison[category]["variance_percentage"] = 100.0
            else:
                category_comparison[category] = {
                    "period1": 0.0,
                    "period2": float(total),
                    "variance": float(total),
                    "variance_percentage": 100.0
                }
        
        return {
            "period1": {
                "start_date": period1_start.isoformat(),
                "end_date": period1_end.isoformat(),
                "total_costs": float(period1_costs)
            },
            "period2": {
                "start_date": period2_start.isoformat(),
                "end_date": period2_end.isoformat(),
                "total_costs": float(period2_costs)
            },
            "comparison": {
                "absolute_variance": float(variance),
                "percentage_variance": float(variance_percentage),
                "trend": "increase" if variance > 0 else "decrease" if variance < 0 else "stable"
            },
            "category_comparison": category_comparison
        }