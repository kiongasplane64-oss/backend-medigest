# app/api/v1/__init__.py
"""
Router principal de l'API v1
"""
from fastapi import APIRouter

from app.api.v1 import auth, users, tenants, superadmin, subscriptions
from .sales import router as sales_router
from .clients import router as clients_router
from app.api.v1.endpoints.products import router as products_router
from app.api.v1.endpoints.stock import router as stock_router 

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(tenants.router)
api_router.include_router(superadmin.router)
api_router.include_router(subscriptions.router)
api_router.include_router(sales_router)
api_router.include_router(clients_router)
api_router.include_router(products_router)
api_router.include_router(stock_router)