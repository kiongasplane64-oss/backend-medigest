from __future__ import annotations

import datetime
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app.db.session import engine

# Routers v1
from app.api.v1.tenants import router as tenant_router
from app.api.v1.auth import router as auth_router
from app.api.v1.subscriptions import router as subscriptions_router
from app.api.v1.subscription_codes import router as subscription_codes_router
from app.api.v1.payments import router as payment_router
from app.api.v1.superadmin import router as superadmin_router
from app.api.v1.endpoints.admin_sync import router as admin_sync_router
from app.api.v1.sync import router as sync_router
from app.api.v1.sales import router as sales_router
from app.api.v1.customers import router as customers_router
from app.api.v1.reports import router as reports_router
from app.api.v1.payments_saas import router as saas_payments_router
from app.api.v1.endpoints.stock import router as stock_router
from app.api.v1 import users
from app.api.v1.categories import router as categories_router
from app.api.v1 import session
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.endpoints.transfers import router as transfers_router
from app.api.v1.orders import router as orders_router
from app.api.v1.capital import router as capital_router
from app.api.v1.endpoints.expenses import router as expenses_router
from app.api.v1.endpoints.branches import router as branches_router
from app.api.v1.endpoints.profit import router as profit_router
from app.api.v1.endpoints.returns import router as returns_router
from app.api.v1.endpoints import invoices

from app.core.startup import init_storage
from app.core.exceptions import setup_exception_handlers

# Routers admin / legacy
from app.api.routes.pharmacies import router as pharmacies_router
from app.api.routes.tenants import router as admin_tenants_router
from app.api.routes.inventory import router as inventory_router

# Middlewares
from app.middleware.tenant_context import TenantContextMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.middleware.audit_middleware import AuditMiddleware
from app.middleware.auth_middleware import AuthMiddleware
from app.middleware.middleware import SubscriptionMiddleware
from app.core.middleware import SubscriptionCheckMiddleware


def utc_iso() -> str:
    return datetime.datetime.utcnow().replace(
        tzinfo=datetime.timezone.utc
    ).isoformat()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("app").setLevel(logging.INFO)


app = FastAPI(
    title="MEDIGEST API",
    description="API pour la gestion des pharmacies MEDIGEST",
    version="1.0.0",
    openapi_url="/api/v1/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    default_response_class=JSONResponse,
)


@app.on_event("startup")
async def startup_event():
    """Initialisation au démarrage"""
    logger.info("🚀 Démarrage de l'application...")
    
    storage_ready = init_storage()
    if storage_ready:
        logger.info("✅ Stockage initialisé avec succès")
    else:
        logger.warning("⚠️ Problème d'initialisation du stockage")
    
    logger.info("✅ Application prête")


# Configuration des gestionnaires d'exceptions
setup_exception_handlers(app)


# ============================================================================
# MIDDLEWARE FORCE JSON
# ============================================================================

class ForceJSONMiddleware(BaseHTTPMiddleware):
    """Middleware qui force les réponses à être en JSON"""
    
    async def dispatch(self, request: Request, call_next):
        # Ajouter l'en-tête Accept: application/json par défaut
        # Note: request._headers est readonly, on utilise request.headers dict à la place
        modified_request = request
        if "Accept" not in request.headers:
            # Créer un nouveau dictionnaire d'en-têtes
            headers = dict(request.headers)
            headers["Accept"] = "application/json"
            # Reconstruire la requête avec les nouveaux headers
            from starlette.datastructures import Headers
            modified_request = Request(request.scope, receive=request.receive)
            modified_request._headers = Headers(headers)
        
        response = await call_next(modified_request)
        
        # Si la réponse est du HTML et que c'est une erreur, la convertir
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400 and "text/html" in content_type:
            try:
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                
                return JSONResponse(
                    status_code=response.status_code,
                    content={
                        "detail": f"Erreur {response.status_code}",
                        "path": str(request.url.path),
                        "method": request.method,
                        "fallback": True
                    }
                )
            except:
                pass
        
        return response


# Ajouter le middleware après les gestionnaires d'exceptions
app.add_middleware(ForceJSONMiddleware)


# ============================================================================
# CORS
# ============================================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "https://medigestpro.net",
        "https://www.medigestpro.net",
        "https://frontend-medigest.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Middlewares personnalisés
app.add_middleware(TenantContextMiddleware)
app.add_middleware(RateLimitMiddleware, request_limit=100, window_seconds=60)
app.add_middleware(AuditMiddleware)
app.add_middleware(AuthMiddleware)


# ============================================================================
# HELPERS ROUTERS
# ============================================================================

def include_router_auto(
    application: FastAPI,
    router: APIRouter,
    *,
    default_prefix: Optional[str] = "/api/v1",
    tags: Optional[list[str]] = None,
) -> None:
    """Inclut un routeur sans casser ses routes existantes."""
    router_prefix = getattr(router, "prefix", "") or ""

    if router_prefix.startswith("/api/v1"):
        final_prefix = None
    elif default_prefix:
        final_prefix = default_prefix
    else:
        final_prefix = None

    if final_prefix:
        application.include_router(router, prefix=final_prefix, tags=tags)
        logger.info("✅ Router inclus: prefix ajouté=%s | router.prefix=%s", final_prefix, router_prefix)
    else:
        application.include_router(router, tags=tags)
        logger.info("✅ Router inclus sans prefix ajouté | router.prefix=%s", router_prefix)


# ============================================================================
# ROUTES SYSTÈME
# ============================================================================

@app.get("/", tags=["System"])
def read_root():
    return {
        "message": "Bienvenue sur l'API MEDIGEST",
        "version": app.version,
        "docs": "/api/docs",
        "health": "/health",
        "timestamp": utc_iso(),
    }


@app.get("/health", tags=["System"])
def health_check():
    return {
        "status": "healthy",
        "service": "medigest-api",
        "version": app.version,
        "timestamp": utc_iso(),
    }


@app.get("/debug/tables", tags=["Debug"])
def debug_tables():
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename;
            """)
        ).fetchall()
    return {"tables": [row[0] for row in rows]}


@app.get("/debug/routes", tags=["Debug"])
def debug_routes():
    routes = []
    for route in app.routes:
        methods = sorted(list(route.methods)) if getattr(route, "methods", None) else []
        routes.append({
            "path": route.path,
            "name": route.name,
            "methods": methods,
        })
    return {"routes": routes}


# ============================================================================
# ROUTERS API V1
# ============================================================================

include_router_auto(app, tenant_router)
include_router_auto(app, auth_router)
include_router_auto(app, subscriptions_router)
include_router_auto(app, payment_router)
include_router_auto(app, sync_router)
include_router_auto(app, sales_router)
include_router_auto(app, customers_router)
include_router_auto(app, reports_router)
include_router_auto(app, saas_payments_router)
include_router_auto(app, superadmin_router)
include_router_auto(app, stock_router, tags=["Stock"])
include_router_auto(app, inventory_router, tags=["Inventory"])
include_router_auto(app, subscription_codes_router)
include_router_auto(app, categories_router)
app.include_router(session.router, prefix="/api/v1")
include_router_auto(app, profit_router, tags=["Bénéfices"]) 
app.include_router(sync_router, prefix="/api/v1", tags=["Synchronization"])
include_router_auto(app, orders_router, tags=["orders"])
include_router_auto(app, transfers_router, tags=["transfers"])
include_router_auto(app, dashboard_router)
include_router_auto(app, capital_router)
app.add_middleware(SubscriptionCheckMiddleware)
app.add_middleware(SubscriptionMiddleware)
include_router_auto(app, admin_sync_router)
include_router_auto(app, invoices.router, tags=["Factures"])
include_router_auto(app, returns_router)


# ============================================================================
# ROUTERS LEGACY / ADMIN
# ============================================================================
app.include_router(branches_router, prefix="/api/v1/branches", tags=["Branches"])
app.include_router(expenses_router, prefix="/api/v1/expenses", tags=["Expenses"])
app.include_router(pharmacies_router, prefix="/api/v1/pharmacies", tags=["Pharmacies"])
include_router_auto(app, admin_tenants_router, default_prefix=None)
include_router_auto(app, users.router, tags=["Users"])