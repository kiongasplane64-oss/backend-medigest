# app/api/v1/__init__.py
"""
Router principal de l'API v1
"""
from fastapi import APIRouter

from app.api.v1 import auth, users, tenants, superadmin, subscriptions, orders
from .sales import router as sales_router
from .customers import router as customers_router
from app.api.v1.endpoints.stock import router as stock_router 
from app.api.v1.subscription_codes import router as subscription_codes_router
from app.api.v1 import sales, categories
from app.api.routes import pharmacies
from app.api.v1.endpoints.branches import router as branches_router
from app.api.v1.sync import router as sync_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.endpoints.transfers import router as transfers_router
from app.api.v1.capital import router as capital_router
from app.api.v1.endpoints.expenses import router as expenses_router



api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(superadmin.router)
api_router.include_router(subscriptions.router)
api_router.include_router(sales_router)
api_router.include_router(customers_router)
api_router.include_router(stock_router)
api_router.include_router(subscription_codes_router)
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(sales.router, prefix="/sales", tags=["Sales"])
api_router.include_router(categories.router, prefix="/categories", tags=["Categories"])
api_router.include_router(pharmacies.router, prefix="/pharmacies", tags=["Pharmacies"])
api_router.include_router(sync_router, prefix="/sync", tags=["Synchronization"]) 
api_router.include_router(dashboard_router, prefix="/dashboard",tags=["Dashboard"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(transfers_router, prefix="/transfers", tags=["Transfers"])
api_router.include_router(capital_router)
api_router.include_router(branches_router, prefix="/api/v1")
api_router.include_router(
    expenses_router,
    prefix="/expenses",
    tags=["Dépenses"]
)
api_router.include_router(users.router)