# app/api/main_routes.py
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

@router.get("/health")
def root_health_check():
    """Endpoint de santé racine"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "MEDIGEST-API",
        "version": "1.0.0",
        "api_routes": [
            "/api/v1/auth",
            "/api/v1/pharmacies",
            "/api/v1/tenants",
            "/api/v1/users"
        ]
    }

@router.get("/api/health")
def api_health_check():
    """Endpoint de santé pour l'API (compatibilité avec auth_service)"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "api_version": "v1",
        "services": {
            "auth": "operational",
            "database": "connected",
            "cache": "active"
        }
    }

@router.get("/api/status")
def api_status():
    """Statut détaillé de l'API"""
    return {
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "environment": "development",
        "endpoints": {
            "auth": "/api/v1/auth",
            "health": "/api/health",
            "api_status": "/api/status"
        }
    }