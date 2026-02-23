from fastapi import APIRouter, Depends, HTTPException, status, Path
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timedelta

from app.api.deps import get_current_user, get_db, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.models.pharmacy import Pharmacy
from app.schemas.pharmacy import (
    PharmacyCreate, 
    PharmacyUpdate, 
    PharmacyResponse,
    PharmacyConfigUpdate
)
from app.utils.pharmacy_utils import PharmacyValidator

router = APIRouter(prefix="/api/v1/pharmacies", tags=["pharmacies"])


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
            "essentiel": {"max_pharmacies": 1, "description": "1 pharmacie"},
            "starter": {"max_pharmacies": 1, "description": "1 pharmacie"},
            "basic": {"max_pharmacies": 1, "description": "1 pharmacie"},
            "professionnel": {"max_pharmacies": 2, "description": "2 pharmacies"},
            "professional": {"max_pharmacies": 2, "description": "2 pharmacies"},
            "entreprise": {"max_pharmacies": 10, "description": "10 pharmacies (illimité)"},
            "enterprise": {"max_pharmacies": 10, "description": "10 pharmacies (illimité)"},
            "premium": {"max_pharmacies": 10, "description": "10 pharmacies (illimité)"},
            "trial": {"max_pharmacies": 1, "description": "1 pharmacie (mode essai)"}
        }
        
        # Recherche insensible à la casse
        plan_lower = plan.lower() if plan else "essentiel"
        for key, value in limits.items():
            if key in plan_lower:
                return value
        
        # Par défaut
        return {"max_pharmacies": 1, "description": "1 pharmacie"}
    
    @staticmethod
    def can_create_pharmacy(
        db: Session, 
        tenant: Tenant, 
        check_active_only: bool = True
    ) -> dict:
        """
        Vérifie si le tenant peut créer une nouvelle pharmacie
        
        Args:
            db: Session SQLAlchemy
            tenant: Tenant object
            check_active_only: Si True, ne compte que les pharmacies actives
        
        Returns:
            dict: {
                "can_create": bool,
                "reason": str,
                "current_count": int,
                "max_allowed": int,
                "remaining": int
            }
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
            "plan_description": limits["description"]
        }
        
        return result


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
    
    return query.offset(skip).limit(limit).all()


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
        "can_create_more": limits_info["can_create"]
    }


@router.post("/", response_model=PharmacyResponse, status_code=status.HTTP_201_CREATED)
def create_pharmacy(
    pharmacy_in: PharmacyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Crée une nouvelle pharmacie"""
    
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
    
    # Créer la pharmacie
    pharmacy = Pharmacy(
        **pharmacy_in.dict(),
        tenant_id=current_tenant.id
    )
    
    db.add(pharmacy)
    db.commit()
    db.refresh(pharmacy)
    
    # Mettre à jour le compteur dans les métadonnées du tenant si nécessaire
    if current_tenant.meta_data is None:
        current_tenant.meta_data = {}
    
    if "pharmacies_stats" not in current_tenant.meta_data:
        current_tenant.meta_data["pharmacies_stats"] = {}
    
    current_tenant.meta_data["pharmacies_stats"]["last_created"] = datetime.utcnow().isoformat()
    current_tenant.meta_data["pharmacies_stats"]["total_created"] = (
        current_tenant.meta_data["pharmacies_stats"].get("total_created", 0) + 1
    )
    
    db.commit()
    
    return pharmacy


@router.patch("/{pharmacy_id}/config")
def update_pharmacy_config(
    pharmacy_id: int,
    config_in: PharmacyConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour la configuration d'une pharmacie"""
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    # Mettre à jour la configuration
    config = pharmacy.config or {}
    config.update(config_in.dict(exclude_unset=True))
    pharmacy.config = config
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(pharmacy)
    
    return {"message": "Configuration mise à jour", "config": pharmacy.config}


@router.delete("/{pharmacy_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pharmacy(
    pharmacy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Désactive une pharmacie (soft delete)"""
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
    pharmacy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Réactive une pharmacie désactivée"""
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
    if not limits_check["can_create"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot reactivate pharmacy: {limits_check['reason']}"
        )
    
    # Réactiver la pharmacie
    pharmacy.is_active = True
    pharmacy.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(pharmacy)
    
    return pharmacy


# ROUTES AVEC PARAMÈTRES TOUJOURS EN DERNIER
@router.get("/{pharmacy_id}", response_model=PharmacyResponse)
def get_pharmacy(
    pharmacy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Récupère une pharmacie spécifique"""
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.id == pharmacy_id,
        Pharmacy.tenant_id == current_tenant.id
    ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée"
        )
    
    return pharmacy


@router.put("/{pharmacy_id}", response_model=PharmacyResponse)
def update_pharmacy(
    pharmacy_id: int,
    pharmacy_in: PharmacyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """Met à jour une pharmacie"""
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
                detail=f"Cannot reactivate pharmacy: {limits_check['reason']}"
            )
    
    update_data = pharmacy_in.dict(exclude_unset=True)
    
    for field, value in update_data.items():
        setattr(pharmacy, field, value)
    
    pharmacy.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pharmacy)
    
    return pharmacy

@router.get("/{pharmacy_id}/online-users")
def get_online_users(
    pharmacy_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère la liste des utilisateurs en ligne pour une pharmacie
    """
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
        User.last_login >= threshold  # Connecté dans les 15 dernières minutes
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
        "pharmacy_name": pharmacy.nom,
        "online_count": len(online_users),
        "users": result,
        "timestamp": datetime.utcnow().isoformat()
    }