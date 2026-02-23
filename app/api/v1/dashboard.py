from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from app.db.session import get_db
from app.models.user import User
from app.models.sale import Sale
from app.models.cost import Cost
from app.models.product import Product
from app.core.security import get_current_user, require_roles

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

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
