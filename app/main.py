from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import datetime
import os
# Import des routeurs
from app.api.v1.tenants import router as tenant_router
from app.api.v1.auth import router as auth_router
from app.api.v1.subscriptions import router as subscription_router
from app.api.v1.payments import router as payment_router
from app.api.v1.sync import router as sync_router
from app.api.v1.sales import router as sales_router
from app.api.v1.stock import router as stock_router
from app.api.v1.clients import router as clients_router
from app.api.v1.reports import router as reports_router
from app.api.v1.payments_saas import router as saas_payments_router
from app.api.routes.pharmacies import router as pharmacies_router
from app.api.routes.tenants import router as admin_tenants_router
from app.api.v1.endpoints.products import router as products_router
from app.api.v1 import users

# Middlewares
from app.middleware.tenant_context import TenantContextMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.middleware.audit_middleware import AuditMiddleware
from app.middleware.auth_middleware import AuthMiddleware


app = FastAPI(
    title="MEDIGEST API",
    description="API pour la gestion des pharmacies MEDIGEST",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# Configuration CORS - À PLACER EN PREMIER
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        # Ajoutez l'URL de production ici
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Middlewares personnalisés (après CORS)
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RateLimitMiddleware, request_limit=100, window_seconds=60)
app.add_middleware(AuditMiddleware)
app.add_middleware(AuthMiddleware)

# Routes de base (une seule fois chacune)
@app.get("/")
def read_root():
    return {
        "message": "Bienvenue sur l'API MEDIGEST",
        "version": "1.0.0",
        "docs": "/api/docs",
        "health": "/health",
        "timestamp": datetime.datetime.utcnow().isoformat()
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "medigest-api",
        "version": "1.0.0",
        "timestamp": datetime.datetime.utcnow().isoformat()
    }

# Inclusion des routeurs avec leurs préfixes
app.include_router(saas_payments_router)
app.include_router(tenant_router)
app.include_router(auth_router)
app.include_router(subscription_router)
app.include_router(payment_router)
app.include_router(sync_router)
app.include_router(sales_router)
app.include_router(stock_router)
app.include_router(clients_router)
app.include_router(reports_router)
app.include_router(pharmacies_router)
app.include_router(admin_tenants_router)

# Routeur users avec préfixe
app.include_router(
    users.router,
    prefix="/api/v1",
    tags=["Users"]
)

# Routeur products avec préfixe
app.include_router(
    products_router,
    prefix="/api/v1",
    tags=["Products"]
)
