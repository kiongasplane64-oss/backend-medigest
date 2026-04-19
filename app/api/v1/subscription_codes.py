# app/api/v1/subscription_codes.py
"""
Gestion des codes d'abonnement pour les pharmacies/branches.
Un abonnement est lié à une pharmacie (branche), pas à un utilisateur.
"""

import logging
import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user
from app.config.plans import PLAN_CONFIG, get_plan_config
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.pharmacy_subscription import PharmacySubscription, SubscriptionPlan, SubscriptionStatus
from app.models.pharmacy import Pharmacy
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCodeCreate,
    ActivateSubscriptionCode
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscription-codes", tags=["Subscription Codes"])


def generate_unique_code(length: int = 8) -> str:
    """Génère un code unique alphanumérique"""
    chars = string.ascii_uppercase + string.digits
    # Exclure les caractères ambigus O, 0, I, 1
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    return ''.join(random.choices(chars, k=length))


def format_code_with_dashes(code: str) -> str:
    """Formate le code avec des tirets tous les 4 caractères"""
    clean = code.replace('-', '').replace(' ', '').upper()
    if len(clean) >= 8:
        return f"{clean[:4]}-{clean[4:8]}"
    return code


def validate_code_logic(db: Session, code: str) -> Dict[str, Any]:
    """
    Logique de validation du code (utilisée par GET et POST)
    """
    # Nettoyer le code
    clean_code = code.strip().upper().replace('-', '').replace(' ', '')
    
    # Essayer différentes formats pour la recherche
    search_variations = [
        code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code,
    ]
    
    code_obj = None
    for search_code in search_variations:
        code_obj = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code
        ).first()
        if code_obj:
            break
    
    if not code_obj:
        return {
            "valid": False,
            "message": "Code invalide."
        }
    
    if not code_obj.is_valid():
        status_text = "expiré" if code_obj.valid_until and datetime.utcnow() > code_obj.valid_until else "déjà utilisé"
        return {
            "valid": False,
            "message": f"Code {status_text}.",
            "status": code_obj.status,
            "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None
        }
    
    return {
        "valid": True,
        "message": "Code valide.",
        "plan": {
            "type": code_obj.plan_type,
            "name": code_obj.plan_name,
            "duration_days": code_obj.duration_days
        },
        "price": float(code_obj.price) if code_obj.price else 0,
        "currency": code_obj.currency or "EUR",
        "valid_until": code_obj.valid_until.isoformat() if code_obj.valid_until else None,
        "code": code_obj.code,
        "pharmacy_id": str(code_obj.pharmacy_id) if code_obj.pharmacy_id else None
    }


def activate_pharmacy_subscription_with_code(
    db: Session,
    pharmacy: Pharmacy,
    code: SubscriptionCode,
    activated_by: User
) -> PharmacySubscription:
    """
    Active un abonnement pour une pharmacie avec un code.
    """
    # Obtenir la configuration du plan
    plan_config = get_plan_config(code.plan_type)
    now = datetime.utcnow()
    end_date = now + timedelta(days=code.duration_days)
    
    # Vérifier si la pharmacie a déjà un abonnement
    existing_sub = db.query(PharmacySubscription).filter(
        PharmacySubscription.pharmacy_id == pharmacy.id
    ).first()
    
    # Convertir le prix (stocké en cents) en float
    price_value = float(code.price / 100) if code.price else 0.0
    
    if existing_sub:
        # Mettre à jour l'abonnement existant
        existing_sub.plan = SubscriptionPlan(code.plan_type)
        existing_sub.plan_name = plan_config["name"]
        existing_sub.start_date = now
        existing_sub.end_date = end_date
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.billing_cycle = "yearly" if code.duration_days >= 365 else "monthly"
        existing_sub.price = price_value
        existing_sub.max_products = plan_config.get("max_products", 0) or 0
        existing_sub.max_users = plan_config.get("max_users", 0) or 0
        existing_sub.max_branches = plan_config.get("max_branches", 0) or 0
        existing_sub.updated_at = now
        subscription = existing_sub
    else:
        # Créer un nouvel abonnement
        subscription = PharmacySubscription(
            pharmacy_id=pharmacy.id,
            plan=SubscriptionPlan(code.plan_type),
            plan_name=plan_config["name"],
            start_date=now,
            end_date=end_date,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle="yearly" if code.duration_days >= 365 else "monthly",
            price=price_value,
            currency=code.currency or "EUR",
            max_products=plan_config.get("max_products", 0) or 0,
            max_users=plan_config.get("max_users", 0) or 0,
            max_branches=plan_config.get("max_branches", 0) or 0
        )
        db.add(subscription)
    
    db.flush()
    return subscription


# =============================================================================
# ENDPOINTS SUPER ADMIN - GESTION DES CODES
# =============================================================================

@router.post("/admin/generate", response_model=Dict[str, Any])
async def generate_subscription_code(
    data: SubscriptionCodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Génère un code d'abonnement pour une pharmacie/branche.
    Accessible uniquement aux super admins.
    
    Peut être associé à :
    - Une pharmacie spécifique
    - Aucune (code générique)
    """
    logger.info(f"Génération de code abonnement par {current_user.email} pour plan {data.plan_type}")
    
    # Vérifier que le plan existe
    if data.plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_plan",
                "message": f"Le plan {data.plan_type} n'existe pas. Plans disponibles: {list(PLAN_CONFIG.keys())}"
            }
        )
    
    # Vérifier la pharmacie si spécifiée
    assigned_pharmacy = None
    if data.pharmacy_id:
        assigned_pharmacy = db.query(Pharmacy).filter(Pharmacy.id == data.pharmacy_id).first()
        if not assigned_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "pharmacy_not_found",
                    "message": "La pharmacie spécifiée n'existe pas."
                }
            )
    
    plan_config = get_plan_config(data.plan_type)
    
    # Générer un code unique
    attempts = 0
    max_attempts = 10
    generated_code = None
    
    while attempts < max_attempts:
        raw_code = generate_unique_code(data.code_length or 8)
        formatted_code = format_code_with_dashes(raw_code)
        
        existing = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == formatted_code
        ).first()
        
        if not existing:
            generated_code = formatted_code
            break
        attempts += 1
    
    if not generated_code:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "code_generation_failed",
                "message": "Impossible de générer un code unique après plusieurs tentatives."
            }
        )
    
    # Calculer la durée en jours
    duration_days = data.duration_days or (365 if data.billing_cycle == "yearly" else 30)
    
    # Calculer le prix (en cents)
    price = data.price
    if not price:
        price_key = f"price_{data.billing_cycle or 'monthly'}"
        price = plan_config.get(price_key, 0)
    
    # Créer le code
    code = SubscriptionCode(
        code=generated_code,
        plan_type=data.plan_type,
        plan_name=plan_config["name"],
        duration_days=duration_days,
        price=int(price * 100) if price else 0,  # Stocker en cents
        currency=data.currency or "EUR",
        valid_from=data.valid_from or datetime.utcnow(),
        valid_until=data.valid_until or (datetime.utcnow() + timedelta(days=data.expiry_days or 90)),
        notes=data.notes,
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
        pharmacy_id=data.pharmacy_id  # Lier à la pharmacie
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    logger.info(f"Code généré avec succès: {generated_code} pour pharmacie: {data.pharmacy_id}")
    
    return {
        "success": True,
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": price,
        "currency": code.currency,
        "duration_days": code.duration_days,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "created_at": code.created_at.isoformat() if code.created_at else None,
        "status": code.status.value if hasattr(code.status, 'value') else code.status,
        "pharmacy_id": str(code.pharmacy_id) if code.pharmacy_id else None,
        "pharmacy_name": assigned_pharmacy.name if assigned_pharmacy else None
    }


@router.get("/admin/list", response_model=Dict[str, Any])
async def list_subscription_codes(
    status: Optional[str] = Query(None),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Liste tous les codes d'abonnement générés.
    Possibilité de filtrer par pharmacie.
    """
    query = db.query(SubscriptionCode)
    
    if status:
        try:
            status_enum = SubscriptionCodeStatus(status)
            query = query.filter(SubscriptionCode.status == status_enum)
        except ValueError:
            pass
    
    if pharmacy_id:
        query = query.filter(SubscriptionCode.pharmacy_id == pharmacy_id)
    
    total = query.count()
    
    codes = query.order_by(SubscriptionCode.created_at.desc())\
                 .offset((page - 1) * limit)\
                 .limit(limit)\
                 .all()
    
    return {
        "total": total,
        "page": page,
        "limit": limit,
        "codes": [
            {
                "id": str(code.id),
                "code": code.code,
                "plan_type": code.plan_type,
                "plan_name": code.plan_name,
                "price": float(code.price) if code.price else 0,
                "currency": code.currency or "EUR",
                "duration_days": code.duration_days,
                "status": code.status.value if hasattr(code.status, 'value') else code.status,
                "valid_until": code.valid_until.isoformat() if code.valid_until else None,
                "created_at": code.created_at.isoformat() if code.created_at else None,
                "activated_by": code.activated_by_user.email if code.activated_by_user else None,
                "activated_at": code.activated_at.isoformat() if code.activated_at else None,
                "created_by": code.created_by_user.email if code.created_by_user else None,
                "pharmacy_id": str(code.pharmacy_id) if code.pharmacy_id else None,
                "pharmacy_name": code.pharmacy.name if code.pharmacy else None
            }
            for code in codes
        ]
    }


@router.get("/admin/{code_id}", response_model=Dict[str, Any])
async def get_subscription_code_details(
    code_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Détails d'un code spécifique.
    """
    code = db.query(SubscriptionCode).filter(SubscriptionCode.id == code_id).first()
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "code_not_found",
                "message": "Code d'abonnement non trouvé."
            }
        )
    
    return {
        "id": str(code.id),
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": float(code.price) if code.price else 0,
        "currency": code.currency or "EUR",
        "duration_days": code.duration_days,
        "status": code.status.value if hasattr(code.status, 'value') else code.status,
        "valid_from": code.valid_from.isoformat() if code.valid_from else None,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "created_at": code.created_at.isoformat() if code.created_at else None,
        "created_by": code.created_by_user.email if code.created_by_user else None,
        "activated_by": code.activated_by_user.email if code.activated_by_user else None,
        "activated_at": code.activated_at.isoformat() if code.activated_at else None,
        "notes": code.notes,
        "is_valid": code.is_valid(),
        "days_remaining": code.days_remaining(),
        "pharmacy_id": str(code.pharmacy_id) if code.pharmacy_id else None,
        "pharmacy_name": code.pharmacy.name if code.pharmacy else None
    }


@router.post("/admin/manual-activate/{pharmacy_id}")
async def manual_activate_pharmacy(
    pharmacy_id: UUID,
    plan_type: str = Query(..., description="Type de plan"),
    duration_days: int = Query(30, ge=1, le=3650, description="Durée en jours"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Activation manuelle d'une pharmacie (paiement cash sans code).
    Le super admin peut activer directement une pharmacie.
    """
    logger.info(f"Activation manuelle de la pharmacie {pharmacy_id} par {current_user.email}")
    
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "pharmacy_not_found",
                "message": "Pharmacie non trouvée."
            }
        )
    
    # Vérifier que le plan existe
    if plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_plan",
                "message": f"Le plan {plan_type} n'existe pas. Plans disponibles: {list(PLAN_CONFIG.keys())}"
            }
        )
    
    plan_config = get_plan_config(plan_type)
    now = datetime.utcnow()
    end_date = now + timedelta(days=duration_days)
    
    # Vérifier si la pharmacie a déjà un abonnement
    existing_sub = db.query(PharmacySubscription).filter(
        PharmacySubscription.pharmacy_id == pharmacy.id
    ).first()
    
    if existing_sub:
        existing_sub.plan = SubscriptionPlan(plan_type)
        existing_sub.plan_name = plan_config["name"]
        existing_sub.start_date = now
        existing_sub.end_date = end_date
        existing_sub.status = SubscriptionStatus.ACTIVE
        existing_sub.billing_cycle = "yearly" if duration_days >= 365 else "monthly"
        existing_sub.price = float(plan_config.get("price_monthly", 0))
        existing_sub.max_products = plan_config.get("max_products", 0) or 0
        existing_sub.max_users = plan_config.get("max_users", 0) or 0
        existing_sub.max_branches = plan_config.get("max_branches", 0) or 0
        existing_sub.updated_at = now
        subscription = existing_sub
    else:
        subscription = PharmacySubscription(
            pharmacy_id=pharmacy.id,
            plan=SubscriptionPlan(plan_type),
            plan_name=plan_config["name"],
            start_date=now,
            end_date=end_date,
            status=SubscriptionStatus.ACTIVE,
            billing_cycle="yearly" if duration_days >= 365 else "monthly",
            price=float(plan_config.get("price_monthly", 0)),
            currency="EUR",
            max_products=plan_config.get("max_products", 0) or 0,
            max_users=plan_config.get("max_users", 0) or 0,
            max_branches=plan_config.get("max_branches", 0) or 0
        )
        db.add(subscription)
    
    db.commit()
    db.refresh(subscription)
    
    return {
        "success": True,
        "message": f"Pharmacie {pharmacy.name} activée manuellement pour {duration_days} jours.",
        "pharmacy": {
            "id": str(pharmacy.id),
            "name": pharmacy.name
        },
        "subscription": {
            "id": str(subscription.id),
            "plan": subscription.plan.value,
            "plan_name": subscription.plan_name,
            "start_date": subscription.start_date.isoformat(),
            "end_date": subscription.end_date.isoformat(),
            "days_remaining": subscription.days_remaining()
        },
        "activated_by": current_user.email,
        "activated_at": datetime.utcnow().isoformat()
    }


# =============================================================================
# ENDPOINTS UTILISATEUR - ACTIVATION POUR UNE PHARMACIE
# =============================================================================

@router.post("/activate", response_model=Dict[str, Any])
async def activate_code_for_pharmacy(
    data: ActivateSubscriptionCode,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Active un abonnement pour la pharmacie active de l'utilisateur.
    Le code est lié à la pharmacie (branche), pas à l'utilisateur.
    """
    logger.info(f"Activation de code par {current_user.email}")
    
    # Vérifier que l'utilisateur a une pharmacie active
    if not current_user.active_pharmacy_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "no_active_pharmacy",
                "message": "Aucune pharmacie active sélectionnée."
            }
        )
    
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == current_user.active_pharmacy_id).first()
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "pharmacy_not_found",
                "message": "Pharmacie non trouvée."
            }
        )
    
    # Nettoyer et chercher le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    search_variations = [
        data.code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code
    ]
    
    code = None
    for search_code in search_variations:
        code = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code,
            SubscriptionCode.status == SubscriptionCodeStatus.PENDING
        ).first()
        if code:
            break
    
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "invalid_code",
                "message": "Code invalide ou déjà utilisé."
            }
        )
    
    # Vérifier que le code peut être utilisé pour cette pharmacie
    if code.pharmacy_id and code.pharmacy_id != pharmacy.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "wrong_pharmacy",
                "message": "Ce code est réservé à une autre pharmacie."
            }
        )
    
    if not code.is_valid():
        status_text = "expiré" if code.valid_until and datetime.utcnow() > code.valid_until else "non disponible"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_expired",
                "message": f"Ce code est {status_text}.",
                "valid_until": code.valid_until.isoformat() if code.valid_until else None
            }
        )
    
    try:
        # Activer l'abonnement pour la pharmacie
        subscription = activate_pharmacy_subscription_with_code(
            db=db,
            pharmacy=pharmacy,
            code=code,
            activated_by=current_user
        )
        
        # Marquer le code comme utilisé
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        code.pharmacy_id = pharmacy.id
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Abonnement activé pour la pharmacie {pharmacy.name}",
            "subscription": {
                "id": str(subscription.id),
                "pharmacy_id": str(pharmacy.id),
                "pharmacy_name": pharmacy.name,
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining()
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type,
                "duration_days": code.duration_days
            }
        }
        
    except Exception as exc:
        db.rollback()
        logger.error(f"Erreur activation: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation. Veuillez contacter le support."
            }
        )


@router.post("/activate-pharmacy/{pharmacy_id}", response_model=Dict[str, Any])
async def activate_pharmacy_with_code(
    pharmacy_id: UUID,
    data: ActivateSubscriptionCode,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Active un abonnement pour une pharmacie spécifique avec un code.
    Réservé aux super admins.
    """
    logger.info(f"Activation de pharmacie {pharmacy_id} avec code par {current_user.email}")
    
    pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "pharmacy_not_found",
                "message": "Pharmacie non trouvée."
            }
        )
    
    # Nettoyer le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    
    search_variations = [
        data.code.strip().upper(),
        format_code_with_dashes(clean_code),
        clean_code
    ]
    
    code = None
    for search_code in search_variations:
        code = db.query(SubscriptionCode).filter(
            SubscriptionCode.code == search_code,
            SubscriptionCode.status == SubscriptionCodeStatus.PENDING
        ).first()
        if code:
            break
    
    if not code:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "invalid_code",
                "message": "Code d'abonnement invalide ou déjà utilisé."
            }
        )
    
    if not code.is_valid():
        status_text = "expiré" if code.valid_until and datetime.utcnow() > code.valid_until else "non disponible"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "code_expired",
                "message": f"Ce code est {status_text}."
            }
        )
    
    try:
        subscription = activate_pharmacy_subscription_with_code(
            db=db,
            pharmacy=pharmacy,
            code=code,
            activated_by=current_user
        )
        
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Abonnement activé pour la pharmacie {pharmacy.name}",
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name
            },
            "subscription": {
                "id": str(subscription.id),
                "plan": subscription.plan.value,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat(),
                "end_date": subscription.end_date.isoformat(),
                "days_remaining": subscription.days_remaining()
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type
            }
        }
        
    except Exception as exc:
        db.rollback()
        logger.error(f"Erreur activation pharmacie: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation."
            }
        )


# =============================================================================
# ENDPOINTS DE VALIDATION (GET ET POST)
# =============================================================================

@router.get("/validate", response_model=Dict[str, Any])
@router.post("/validate", response_model=Dict[str, Any])
async def validate_code(
    code: str = Query(None, description="Code à valider"),
    db: Session = Depends(get_db),
    data: Optional[ActivateSubscriptionCode] = None,
) -> Any:
    """
    Valide un code sans l'activer.
    Accepte GET (avec query param) ou POST (avec body).
    """
    # Déterminer le code à valider
    code_to_validate = None
    if data:
        code_to_validate = data.code
    elif code:
        code_to_validate = code
    
    if not code_to_validate:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_code",
                "message": "Code d'abonnement requis."
            }
        )
    
    logger.info(f"Validation de code: {code_to_validate}")
    result = validate_code_logic(db, code_to_validate)
    
    if not result["valid"]:
        logger.warning(f"Code invalide: {code_to_validate} - {result['message']}")
    
    return result


# =============================================================================
# ENDPOINT DE DÉBOGAGE
# =============================================================================

@router.post("/debug/generate-test", include_in_schema=False)
async def debug_generate_test_code(
    plan_type: str = "professional",
    pharmacy_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Endpoint de débogage pour générer un code de test.
    (Non documenté, accessible seulement en développement)
    """
    if plan_type not in PLAN_CONFIG:
        plan_type = "professional"
    
    raw_code = generate_unique_code(8)
    formatted_code = format_code_with_dashes(raw_code)
    
    plan_config = get_plan_config(plan_type)
    
    code = SubscriptionCode(
        code=formatted_code,
        plan_type=plan_type,
        plan_name=plan_config["name"],
        duration_days=30,
        price=int(plan_config.get("price_monthly", 0) * 100),
        currency="EUR",
        valid_from=datetime.utcnow(),
        valid_until=datetime.utcnow() + timedelta(days=90),
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
        notes="Code de test généré automatiquement",
        pharmacy_id=pharmacy_id
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    return {
        "success": True,
        "message": "Code d'abonnement généré",
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "pharmacy_id": str(code.pharmacy_id) if code.pharmacy_id else None
    }