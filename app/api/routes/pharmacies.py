from fastapi import APIRouter, Depends, HTTPException, status, Path
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import json
from uuid import UUID
import pytz
from zoneinfo import ZoneInfo  # Python 3.9+

from app.api.deps import get_current_user, get_db, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.schemas.pharmacy import (
    PharmacyCreate, 
    PharmacyUpdate, 
    PharmacyResponse,
    PharmacyConfigUpdate,
    PharmacyConfigResponse,
    CurrencyConfig,
    WorkingHoursConfig,
    AutomaticPricingConfig,
    BranchConfig,
    ThemeConfig
)
from app.utils.pharmacy_utils import PharmacyValidator

router = APIRouter(prefix="/api/v1/pharmacies", tags=["pharmacies"])

# Fuseaux horaires supportés
SUPPORTED_TIMEZONES = {
    "kinshasa": "Africa/Kinshasa",  # UTC+1 (pas d'heure d'été)
    "lubumbashi": "Africa/Lubumbashi",  # UTC+2
    "paris": "Europe/Paris",  # UTC+1/UTC+2 (avec heure d'été)
    "rdc": "Africa/Kinshasa",  # Par défaut RDC
    "congo": "Africa/Kinshasa"
}

def get_pharmacy_timezone(pharmacy_config: dict) -> str:
    """
    Récupère le fuseau horaire configuré pour la pharmacie
    Par défaut: Africa/Kinshasa (UTC+1)
    """
    # Vous pouvez ajouter un champ timezone dans la config si nécessaire
    # Pour l'instant, on utilise Kinshasa par défaut
    return "Africa/Kinshasa"

def get_current_time_in_timezone(timezone_str: str = "Africa/Kinshasa") -> datetime:
    """
    Retourne l'heure actuelle dans le fuseau horaire spécifié
    """
    try:
        tz = pytz.timezone(timezone_str)
        return datetime.now(tz)
    except:
        # Fallback sur UTC si le fuseau n'est pas trouvé
        return datetime.now(pytz.UTC)

class PharmacyLimits:
    """Définit les limites de pharmacies selon le plan d'abonnement"""
    
    @staticmethod
    def get_limits_for_plan(plan: str) -> dict:
        """
        Retourne les limites selon le plan:
        - essentiel: 1 pharmacie
        - professionnel: 2 pharmacies
        - entreprise: 10 pharmacies (illimité jusqu'à 10)
        """
        limits = {
            "essentiel": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0, "description": "1 pharmacie"},
            "starter": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0, "description": "1 pharmacie"},
            "basic": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0, "description": "1 pharmacie"},
            "professionnel": {"max_pharmacies": 2, "max_branches_per_pharmacy": 1, "description": "2 pharmacies, 1 succursale"},
            "professional": {"max_pharmacies": 2, "max_branches_per_pharmacy": 1, "description": "2 pharmacies, 1 succursale"},
            "entreprise": {"max_pharmacies": 10, "max_branches_per_pharmacy": 5, "description": "10 pharmacies, 5 succursales"},
            "enterprise": {"max_pharmacies": 10, "max_branches_per_pharmacy": 5, "description": "10 pharmacies, 5 succursales"},
            "premium": {"max_pharmacies": 10, "max_branches_per_pharmacy": 5, "description": "10 pharmacies, 5 succursales"},
            "trial": {"max_pharmacies": 1, "max_branches_per_pharmacy": 0, "description": "1 pharmacie (mode essai)"}
        }
        
        # Recherche insensible à la casse
        plan_lower = plan.lower() if plan else "essentiel"
        for key, value in limits.items():
            if key in plan_lower:
                return value
        
        # Par défaut
        return {"max_pharmacies": 1, "max_branches_per_pharmacy": 0, "description": "1 pharmacie"}
    
    @staticmethod
    def can_create_pharmacy(
        db: Session, 
        tenant: Tenant, 
        check_active_only: bool = True
    ) -> dict:
        """
        Vérifie si le tenant peut créer une nouvelle pharmacie
        """
        # Compter les pharmacies actuelles
        query = db.query(Pharmacy).filter(Pharmacy.tenant_id == tenant.id)
        
        if check_active_only:
            query = query.filter(Pharmacy.is_active == True)
        
        current_count = query.count()
        
        # Récupérer la limite selon le plan
        plan = tenant.current_plan or "essentiel"
        limits = PharmacyLimits.get_limits_for_plan(plan)
        max_allowed = limits["max_pharmacies"]
        
        can_create = current_count < max_allowed
        
        result = {
            "can_create": can_create,
            "reason": "" if can_create else f"Limite de {max_allowed} pharmacies atteinte pour le plan {plan}",
            "current_count": current_count,
            "max_allowed": max_allowed,
            "remaining": max(0, max_allowed - current_count),
            "plan": plan,
            "plan_description": limits["description"],
            "max_branches_per_pharmacy": limits["max_branches_per_pharmacy"]
        }
        
        return result
    
    @staticmethod
    def can_create_branch(pharmacy: Pharmacy, tenant: Tenant) -> dict:
        """
        Vérifie si une pharmacie peut créer une succursale
        """
        config = pharmacy.config or {}
        branch_config = config.get("branchConfig", {})
        current_branches = branch_config.get("currentBranches", 0)
        
        limits = PharmacyLimits.get_limits_for_plan(tenant.current_plan or "essentiel")
        max_branches = limits["max_branches_per_pharmacy"]
        
        can_create = current_branches < max_branches
        
        return {
            "can_create": can_create,
            "current_branches": current_branches,
            "max_branches_allowed": max_branches,
            "remaining": max(0, max_branches - current_branches),
            "reason": "" if can_create else f"Limite de {max_branches} succursales atteinte"
        }


# ROUTES STATIQUES EN PREMIER
@router.get("/", response_model=List[PharmacyResponse])
def get_pharmacies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True
):
    """Récupère toutes les pharmacies du tenant"""
    query = db.query(Pharmacy).filter(Pharmacy.tenant_id == current_tenant.id)
    
    if active_only:
        query = query.filter(Pharmacy.is_active == True)
    
    pharmacies = query.offset(skip).limit(limit).all()
    
    # Convertir explicitement les UUIDs en strings
    result = []
    for pharmacy in pharmacies:
        pharmacy_dict = {
            "id": str(pharmacy.id),
            "tenant_id": str(pharmacy.tenant_id),
            "nom": pharmacy.name,
            "name": pharmacy.name,
            "license_number": pharmacy.license_number,
            "address": pharmacy.address,
            "city": pharmacy.city,
            "country": pharmacy.country,
            "phone": pharmacy.phone,
            "email": pharmacy.email,
            "is_active": pharmacy.is_active,
            "opening_hours": pharmacy.opening_hours,
            "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
            "pharmacist_license": pharmacy.pharmacist_license,
            "config": pharmacy.config,
            "created_at": pharmacy.created_at,
            "updated_at": pharmacy.updated_at
        }
        result.append(pharmacy_dict)
    
    return result


@router.get("/limits", response_model=dict)
def get_pharmacy_limits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère les limites de pharmacies pour le tenant actuel"""
    limits_info = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    
    return {
        "tenant_id": str(current_tenant.id),
        "tenant_name": current_tenant.nom_pharmacie,
        "current_plan": current_tenant.current_plan or "essentiel",
        "limits": PharmacyLimits.get_limits_for_plan(current_tenant.current_plan or "essentiel"),
        "current_pharmacies_count": limits_info["current_count"],
        "max_pharmacies_allowed": limits_info["max_allowed"],
        "remaining_pharmacies": limits_info["remaining"],
        "can_create_more": limits_info["can_create"],
        "max_branches_per_pharmacy": limits_info["max_branches_per_pharmacy"]
    }


@router.post("/", response_model=PharmacyResponse, status_code=status.HTTP_201_CREATED)
def create_pharmacy(
    pharmacy_in: PharmacyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Crée une nouvelle pharmacie avec configuration par défaut"""
    
    # Vérifier les limites selon le plan
    limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    
    if not limits_check["can_create"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=limits_check["reason"]
        )
    
    # Valider le numéro de licence
    if not PharmacyValidator.validate_license_number(pharmacy_in.license_number, pharmacy_in.country):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Numéro de licence invalide"
        )
    
    # Vérifier l'unicité du numéro de licence
    existing = db.query(Pharmacy).filter(
        Pharmacy.license_number == pharmacy_in.license_number
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ce numéro de licence est déjà utilisé"
        )
    
    # Configuration par défaut avec heures en heure locale (Paris/RDC)
    default_config = {
        "pharmacyInfo": {
            "name": pharmacy_in.nom or pharmacy_in.name,
            "address": pharmacy_in.address or "",
            "phone": pharmacy_in.phone or "",
            "email": pharmacy_in.email or "",
            "licenseNumber": pharmacy_in.license_number
        },
        "currencies": [
            {"code": "CDF", "symbol": "FC", "isActive": True, "exchangeRate": 2500},
            {"code": "USD", "symbol": "$", "isActive": True, "exchangeRate": 1}
        ],
        "primaryCurrency": "CDF",
        "taxRate": 16,
        "lowStockThreshold": 10,
        "expiryWarningDays": 90,
        "allowNegativeStock": False,
        "workingHours": {
            "enabled": True,
            "startTime": "08:00",  # Heure locale (Paris/RDC)
            "endTime": "20:00",     # Heure locale (Paris/RDC)
            "overtimeEndTime": "22:00",  # Heure locale (Paris/RDC)
            "daysOff": {
                "monday": True,
                "tuesday": True,
                "wednesday": True,
                "thursday": True,
                "friday": True,
                "saturday": True,
                "sunday": False
            },
            "timezone": "Africa/Kinshasa"  # Fuseau horaire par défaut
        },
        "productReturnDays": 30,
        "marginConfig": {
            "defaultMargin": 25,
            "minMargin": 10,
            "maxMargin": 50
        },
        "automaticPricing": {
            "enabled": False,
            "method": "percentage",
            "value": 25
        },
        "theme": "system",
        "initialCapital": 0,
        "branchConfig": {
            "maxBranches": limits_check["max_branches_per_pharmacy"],
            "currentBranches": 0
        },
        "createdAt": datetime.utcnow().isoformat(),
        "updatedAt": datetime.utcnow().isoformat()
    }
    
    # Créer la pharmacie
    pharmacy = Pharmacy(
        name=pharmacy_in.nom or pharmacy_in.name,
        license_number=pharmacy_in.license_number,
        address=pharmacy_in.address,
        city=pharmacy_in.city,
        country=pharmacy_in.country,
        phone=pharmacy_in.phone,
        email=pharmacy_in.email,
        is_active=pharmacy_in.is_active,
        opening_hours=pharmacy_in.opening_hours,
        pharmacist_in_charge=pharmacy_in.pharmacist_in_charge,
        pharmacist_license=pharmacy_in.pharmacist_license,
        config=default_config,
        tenant_id=current_tenant.id
    )
    
    db.add(pharmacy)
    db.commit()
    db.refresh(pharmacy)
    
    # Mettre à jour les statistiques du tenant
    if current_tenant.meta_data is None:
        current_tenant.meta_data = {}
    
    if "pharmacies_stats" not in current_tenant.meta_data:
        current_tenant.meta_data["pharmacies_stats"] = {}
    
    current_tenant.meta_data["pharmacies_stats"]["last_created"] = datetime.utcnow().isoformat()
    current_tenant.meta_data["pharmacies_stats"]["total_created"] = (
        current_tenant.meta_data["pharmacies_stats"].get("total_created", 0) + 1
    )
    
    db.commit()
    
    # Convertir explicitement les UUIDs en strings pour la réponse
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


# ROUTES DE CONFIGURATION
@router.get("/{pharmacy_id}/config", response_model=PharmacyConfigResponse)
def get_pharmacy_config(
    pharmacy_id: str = Path(..., description="ID de la pharmacie (UUID)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère la configuration complète d'une pharmacie"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    return {
        "pharmacy_id": pharmacy_id,
        "config": pharmacy.config or {},
        "updated_at": pharmacy.updated_at
    }


@router.patch("/{pharmacy_id}/config", response_model=PharmacyConfigResponse)
def update_pharmacy_config(
    pharmacy_id: str,
    config_in: PharmacyConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration d'une pharmacie"""
    # Validation de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Récupérer la config actuelle ou initialiser
    current_config = pharmacy.config or {}
    
    # Mettre à jour uniquement les champs fournis
    update_data = config_in.dict(exclude_unset=True, exclude_none=True)
    
    # Fusion profonde des données
    def deep_merge(original, updates):
        for key, value in updates.items():
            if isinstance(value, dict) and key in original and isinstance(original[key], dict):
                deep_merge(original[key], value)
            else:
                original[key] = value
        return original
    
    # Appliquer les mises à jour
    updated_config = deep_merge(current_config.copy(), update_data)
    
    # Ajouter la date de mise à jour
    updated_config["updatedAt"] = datetime.utcnow().isoformat()
    
    # Mettre à jour la pharmacie
    pharmacy.config = updated_config
    pharmacy.updated_at = datetime.utcnow()
    
    # Commit explicite
    db.commit()
    db.refresh(pharmacy)
    
    return {
        "pharmacy_id": pharmacy_id,
        "config": pharmacy.config,
        "updated_at": pharmacy.updated_at
    }

@router.patch("/{pharmacy_id}/config/currencies")
def update_currencies_config(
    pharmacy_id: str,
    currencies: List[CurrencyConfig],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration des devises"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # VALIDATION DES TAUX DE CHANGE
    for currency in currencies:
        # Vérifier que le taux de change est valide
        if currency.exchangeRate <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Le taux de change pour {currency.code} doit être supérieur à 0"
            )
        
        # Pour USD, le taux doit être exactement 1 (devise de référence)
        if currency.code == "USD" and currency.exchangeRate != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le taux de change pour USD doit être 1 (devise de référence)"
            )
    
    # S'assurer qu'au moins une devise est active
    if not any(c.isActive for c in currencies):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Au moins une devise doit être active"
        )
    
    config = pharmacy.config or {}
    config["currencies"] = [c.dict() for c in currencies]
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Configuration des devises mise à jour", "currencies": currencies}


@router.patch("/{pharmacy_id}/config/working-hours")
def update_working_hours(
    pharmacy_id: str,
    working_hours: WorkingHoursConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour les heures de service"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["workingHours"] = working_hours.dict()
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Heures de service mises à jour", "working_hours": working_hours}


@router.patch("/{pharmacy_id}/config/pricing")
def update_pricing_config(
    pharmacy_id: str,
    pricing: AutomaticPricingConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration des prix"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["automaticPricing"] = pricing.dict()
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Configuration des prix mise à jour", "pricing": pricing}


@router.patch("/{pharmacy_id}/config/theme")
def update_theme(
    pharmacy_id: str,
    theme: ThemeConfig,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour le thème de l'application"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    config = pharmacy.config or {}
    config["theme"] = theme.theme
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Thème mis à jour", "theme": theme.theme}


@router.post("/{pharmacy_id}/branches", response_model=dict)
def create_branch(
    pharmacy_id: str,
    branch_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Crée une nouvelle succursale pour une pharmacie"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Vérifier les limites de succursales
    limits_check = PharmacyLimits.can_create_branch(pharmacy, current_tenant)
    
    if not limits_check["can_create"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=limits_check["reason"]
        )
    
    config = pharmacy.config or {}
    branch_config = config.get("branchConfig", {
        "maxBranches": limits_check["max_branches_allowed"],
        "currentBranches": 0,
        "branches": []
    })
    
    # Ajouter la nouvelle succursale
    if "branches" not in branch_config:
        branch_config["branches"] = []
    
    new_branch = {
        "id": f"branch_{len(branch_config['branches']) + 1}",
        "name": branch_data.get("name", f"Succursale {len(branch_config['branches']) + 1}"),
        "address": branch_data.get("address", ""),
        "phone": branch_data.get("phone", ""),
        "email": branch_data.get("email", ""),
        "manager": branch_data.get("manager", ""),
        "created_at": datetime.utcnow().isoformat(),
        "is_active": True
    }
    
    branch_config["branches"].append(new_branch)
    branch_config["currentBranches"] = len(branch_config["branches"])
    branch_config["maxBranches"] = limits_check["max_branches_allowed"]
    
    config["branchConfig"] = branch_config
    config["updatedAt"] = datetime.utcnow().isoformat()
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    
    return {
        "message": "Succursale créée avec succès",
        "branch": new_branch,
        "remaining": limits_check["remaining"] - 1,
        "current_branches": branch_config["currentBranches"],
        "max_allowed": limits_check["max_branches_allowed"]
    }

@router.get("/{pharmacy_id}/service-status")
def check_service_status(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Vérifie si la pharmacie est en service selon les heures configurées.
    
    Logique CORRIGÉE:
    - workingHours.enabled = True -> Les restrictions sont actives
    - daysOff = Jours OUVERTS (True = ouvert, False = fermé) - COHERENT AVEC LE FRONTEND
    - La pharmacie est en service si:
        * Aujourd'hui est un jour OUVERT (daysOff[aujourd'hui] = True)
        * ET l'heure actuelle est entre startTime et endTime
    """
    # Validation de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    # Récupérer la pharmacie
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Récupérer la configuration
    config = pharmacy.config or {}
    working_hours = config.get("workingHours", {})
    
    # Logging pour débogage
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"=== VÉRIFICATION SERVICE PHARMACIE {pharmacy_id} ===")
    logger.info(f"Configuration workingHours: {working_hours}")
    
    # Si les restrictions ne sont pas activées, toujours en service
    if not working_hours.get("enabled", True):
        logger.info("Restrictions désactivées -> SERVICE DISPONIBLE")
        return {
            "in_service": True,
            "restrictions_enabled": False,
            "message": "Service toujours disponible (pas de restriction horaire)",
            "current_time_utc": datetime.now(pytz.UTC).isoformat(),
        }
    
    # Récupérer le fuseau horaire de la pharmacie
    timezone_str = working_hours.get("timezone", "Africa/Kinshasa")
    
    try:
        tz = pytz.timezone(timezone_str)
        now_local = datetime.now(tz)
        now_utc = datetime.now(pytz.UTC)
        logger.info(f"Heure locale ({timezone_str}): {now_local.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Heure UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        logger.error(f"Erreur fuseau horaire: {e}, utilisation UTC")
        tz = pytz.UTC
        now_local = datetime.now(pytz.UTC)
        now_utc = now_local
        timezone_str = "UTC"
    
    # Heure actuelle en minutes depuis minuit
    current_minutes = now_local.hour * 60 + now_local.minute
    current_day = now_local.strftime("%A").lower()
    
    logger.info(f"Jour actuel: {current_day}")
    logger.info(f"Heure actuelle: {now_local.hour:02d}:{now_local.minute:02d} ({current_minutes} minutes)")
    
    # Récupérer la configuration des jours de service
    # CORRECTION: daysOff: True = jour OUVERT (comme dans le frontend)
    days_off = working_hours.get("daysOff", {})
    logger.info(f"Configuration daysOff (True = OUVERT): {days_off}")
    
    # Si days_off est vide, utiliser les valeurs par défaut (ouvert du lundi au samedi, fermé dimanche)
    if not days_off:
        days_off = {
            "monday": True,    # Lundi OUVERT
            "tuesday": True,   # Mardi OUVERT
            "wednesday": True, # Mercredi OUVERT
            "thursday": True,  # Jeudi OUVERT
            "friday": True,    # Vendredi OUVERT
            "saturday": True,  # Samedi OUVERT
            "sunday": False    # Dimanche FERMÉ
        }
        logger.info(f"Utilisation des valeurs par défaut: {days_off}")
    
    # CORRECTION: Vérifier si aujourd'hui est un jour OUVERT
    is_open_today = days_off.get(current_day, False)  # True = ouvert
    is_working_day = is_open_today  # C'est un jour travaillé si c'est ouvert
    
    logger.info(f"Aujourd'hui est ouvert? {is_open_today} -> Jour travaillé? {is_working_day}")
    
    # Récupérer les heures d'ouverture
    start_time_str = working_hours.get("startTime", "08:00")
    end_time_str = working_hours.get("endTime", "20:00")
    
    logger.info(f"Heures d'ouverture: {start_time_str} - {end_time_str}")
    
    try:
        start_parts = start_time_str.split(":")
        end_parts = end_time_str.split(":")
        start_minutes = int(start_parts[0]) * 60 + int(start_parts[1])
        end_minutes = int(end_parts[0]) * 60 + int(end_parts[1])
    except (ValueError, IndexError) as e:
        logger.error(f"Erreur format heure: {e}, utilisation valeurs par défaut")
        start_minutes = 8 * 60  # 08:00
        end_minutes = 20 * 60    # 20:00
        start_time_str = "08:00"
        end_time_str = "20:00"
    
    # Vérifier si l'heure actuelle est dans la plage d'ouverture
    # Gestion du cas où l'heure de fin est après minuit (ex: 20:00 - 02:00)
    if end_minutes < start_minutes:
        # Service qui passe minuit
        if current_minutes >= start_minutes or current_minutes <= end_minutes:
            is_within_hours = True
            logger.info(f"Heure {current_minutes} dans plage nuit ({start_minutes}-{end_minutes})")
        else:
            is_within_hours = False
            logger.info(f"Heure {current_minutes} hors plage nuit ({start_minutes}-{end_minutes})")
    else:
        # Cas normal
        is_within_hours = start_minutes <= current_minutes <= end_minutes
        logger.info(f"Heure {current_minutes} dans plage {start_minutes}-{end_minutes}? {is_within_hours}")
    
    # CORRECTION: Déterminer si la pharmacie est en service
    in_service = is_working_day and is_within_hours
    logger.info(f"Résultat final - Jour ouvert: {is_working_day}, Dans heures: {is_within_hours} -> EN SERVICE? {in_service}")
    
    # Calculer le prochain service pour aider l'utilisateur
    next_service_info = calculate_next_service_time(
        current_day=current_day,
        current_minutes=current_minutes,
        working_hours=working_hours,
        is_working_day=is_working_day,
        start_minutes=start_minutes,
        end_minutes=end_minutes,
        days_off=days_off
    )
    
    logger.info(f"Prochain service: {next_service_info}")
    logger.info("========================================")
    
    # Construire la réponse
    response = {
        "in_service": in_service,
        "restrictions_enabled": True,
        "current_time_utc": now_utc.isoformat(),
        "current_time_local": now_local.isoformat(),
        "timezone": timezone_str,
        "current_day": current_day,
        "is_working_day": is_working_day,
        "is_open_today": is_open_today,
        "is_within_hours": is_within_hours,
        "working_hours": {
            "start": start_time_str,
            "end": end_time_str,
            "overtime": working_hours.get("overtimeEndTime")
        },
        "message": "✅ En service" if in_service else "❌ Hors service",
    }
    
    # Ajouter les informations sur le prochain service si disponible
    if next_service_info:
        response["next_service_time"] = next_service_info
    
    return response
# ==================== FONCTIONS UTILITAIRES ====================

def calculate_next_service_time(
    current_day: str,
    current_minutes: int,
    working_hours: dict,
    is_working_day: bool,
    start_minutes: int,
    end_minutes: int,
    days_off: dict
) -> str | None:
    """
    Calcule le prochain moment où la pharmacie sera en service.
    
    CORRECTION: days_off[day] = True signifie jour OUVERT
    """
    # Récupérer l'heure d'ouverture formatée pour l'affichage
    start_time_str = working_hours.get("startTime", "08:00")
    
    # CAS 1: Jour ouvert mais avant l'ouverture
    if is_working_day and current_minutes < start_minutes:
        return f"{start_time_str} (aujourd'hui)"
    
    # CAS 2: Jour ouvert mais après la fermeture
    if is_working_day and current_minutes > end_minutes:
        return find_next_open_day(current_day, start_time_str, days_off)
    
    # CAS 3: Jour fermé
    if not is_working_day:
        return find_next_open_day(current_day, start_time_str, days_off)
    
    # CAS 4: Déjà en service (aucun message à retourner)
    return None


def find_next_open_day(current_day: str, start_time: str, days_off: dict) -> str:
    """
    Trouve le prochain jour OUVERT dans la semaine.
    
    CORRECTION: days_off[day] = True signifie jour OUVERT
    
    Args:
        current_day: Jour actuel en anglais
        start_time: Heure d'ouverture formatée (HH:MM)
        days_off: Dictionnaire des jours ouverts (True = ouvert)
    
    Returns:
        str: Description du prochain jour ouvert avec l'heure
    """
    # Ordre des jours de la semaine
    days_order = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    
    # Traduction des jours en français pour l'affichage
    day_names_fr = {
        "monday": "lundi", 
        "tuesday": "mardi", 
        "wednesday": "mercredi",
        "thursday": "jeudi", 
        "friday": "vendredi", 
        "saturday": "samedi",
        "sunday": "dimanche"
    }
    
    # Trouver l'index du jour actuel
    try:
        current_index = days_order.index(current_day)
    except ValueError:
        # Si le jour n'est pas reconnu, on part de lundi
        current_index = 0
    
    # Parcourir les 7 prochains jours
    for i in range(1, 8):  # i = 1 à 7
        next_index = (current_index + i) % 7
        next_day = days_order[next_index]
        
        # CORRECTION: Vérifier si ce jour est OUVERT
        # Un jour est ouvert si days_off[next_day] = True
        is_open = days_off.get(next_day, False)
        
        if is_open:
            day_name_fr = day_names_fr.get(next_day, next_day)
            
            # Formatage du message selon le délai
            if i == 1:
                return f"{start_time} demain"
            else:
                return f"{start_time} {day_name_fr}"
    
    # Si aucun jour n'est trouvé (tous les jours sont fermés)
    return "aucun jour d'ouverture configuré"

# ==================== FIN DES FONCTIONS UTILITAIRES ====================

# ROUTES CRUD STANDARD
@router.delete("/{pharmacy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Désactive une pharmacie (soft delete)"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Désactiver plutôt que supprimer
    pharmacy.is_active = False
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()


@router.post("/{pharmacy_id}/reactivate", response_model=PharmacyResponse)
def reactivate_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Réactive une pharmacie désactivée"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Vérifier les limites avant de réactiver
    limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
    if not limits_check["can_create"] and not pharmacy.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Impossible de réactiver: {limits_check['reason']}"
        )
    
    # Réactiver la pharmacie
    pharmacy.is_active = True
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(pharmacy)
    
    # Convertir explicitement les UUIDs en strings pour la réponse
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


# ROUTES AVEC PARAMÈTRES TOUJOURS EN DERNIER
@router.get("/{pharmacy_id}", response_model=PharmacyResponse)
def get_pharmacy(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère une pharmacie spécifique"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Convertir explicitement les UUIDs en strings pour la réponse
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


@router.put("/{pharmacy_id}", response_model=PharmacyResponse)
def update_pharmacy(
    pharmacy_id: str,
    pharmacy_in: PharmacyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour une pharmacie"""
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Si on tente de réactiver une pharmacie désactivée, vérifier les limites
    if pharmacy_in.is_active is True and pharmacy.is_active is False:
        limits_check = PharmacyLimits.can_create_pharmacy(db, current_tenant)
        if not limits_check["can_create"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Impossible de réactiver: {limits_check['reason']}"
            )
    
    update_data = pharmacy_in.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        if field == 'name' or field == 'nom':
            setattr(pharmacy, 'name', value)
        else:
            setattr(pharmacy, field, value)
    
    pharmacy.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pharmacy)
    
    # Convertir explicitement les UUIDs en strings pour la réponse
    pharmacy_dict = {
        "id": str(pharmacy.id),
        "tenant_id": str(pharmacy.tenant_id),
        "nom": pharmacy.name,
        "name": pharmacy.name,
        "license_number": pharmacy.license_number,
        "address": pharmacy.address,
        "city": pharmacy.city,
        "country": pharmacy.country,
        "phone": pharmacy.phone,
        "email": pharmacy.email,
        "is_active": pharmacy.is_active,
        "opening_hours": pharmacy.opening_hours,
        "pharmacist_in_charge": pharmacy.pharmacist_in_charge,
        "pharmacist_license": pharmacy.pharmacist_license,
        "config": pharmacy.config,
        "created_at": pharmacy.created_at,
        "updated_at": pharmacy.updated_at
    }
    
    return pharmacy_dict


@router.get("/{pharmacy_id}/online-users")
def get_online_users(
    pharmacy_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère la liste des utilisateurs en ligne pour une pharmacie
    """
    # Validation optionnelle de l'UUID
    try:
        UUID(pharmacy_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID de pharmacie invalide"
        )
    
    # Vérifier que la pharmacie appartient au tenant de l'utilisateur
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_user.tenant_id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Calculer le seuil d'inactivité (15 minutes)
    threshold = datetime.utcnow() - timedelta(minutes=15)
    
    # Récupérer les utilisateurs avec dernière activité récente
    online_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.pharmacy_id == pharmacy_id,
        User.actif == True,
        User.last_login >= threshold
    ).all()
    
    result = []
    for user in online_users:
        # Calculer la durée de connexion
        if user.last_login:
            login_duration = datetime.utcnow() - user.last_login
            duration_minutes = int(login_duration.total_seconds() / 60)
            duration_text = f"{duration_minutes} min"
        else:
            duration_text = "N/A"
        
        result.append({
            "id": str(user.id),
            "nom_complet": user.nom_complet,
            "email": user.email,
            "role": user.role,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_duration": duration_text,
            "status": "online"
        })
    
    return {
        "pharmacy_id": pharmacy_id,
        "pharmacy_name": pharmacy.name,
        "online_count": len(online_users),
        "users": result,
        "timestamp": datetime.utcnow().isoformat()
    }