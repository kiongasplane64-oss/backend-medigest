# app/api/v1/subscription_codes.py
import logging
import random
import string
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_current_active_user, get_super_admin_user
from app.models.subscription_code import SubscriptionCode, SubscriptionCodeStatus
from app.models.user import User
from app.schemas.subscription import (
    SubscriptionCodeCreate, 
    SubscriptionCodeResponse,
    ActivateSubscriptionCode,
    ValidateCodeResponse  # À créer si nécessaire
)
from app.services.subscription_service import (
    PLAN_CONFIG,
    activate_subscription_with_code,
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
    # Nettoyer d'abord le code
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
        code.strip().upper(),  # Original
        format_code_with_dashes(clean_code),  # Formaté XXXX-XXXX
        clean_code,  # Sans tirets
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
        "code": code_obj.code  # Retourner le code formaté
    }

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
    Génère un code d'abonnement pour paiement cash.
    Accessible uniquement aux super admins.
    """
    logger.info(f"Génération de code abonnement par {current_user.email} pour plan {data.plan_type}")
    
    # Vérifier que le plan existe
    if data.plan_type not in PLAN_CONFIG:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_plan",
                "message": f"Le plan {data.plan_type} n'existe pas."
            }
        )
    
    plan_config = PLAN_CONFIG[data.plan_type]
    
    # Générer un code unique
    attempts = 0
    max_attempts = 10
    generated_code = None
    
    while attempts < max_attempts:
        raw_code = generate_unique_code(data.code_length or 8)
        formatted_code = format_code_with_dashes(raw_code)
        
        # Vérifier si le code existe déjà
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
    duration_days = data.duration_days
    if not duration_days:
        duration_days = 365 if data.billing_cycle == "yearly" else 30
    
    # Calculer le prix
    price = data.price
    if not price:
        price_key = f"price_{data.billing_cycle or 'monthly'}"
        price = plan_config.get(price_key, 0)
    
    # Créer le code
    code = SubscriptionCode(
        code=generated_code,
        plan_type=data.plan_type,
        plan_name=plan_config.get("name", data.plan_type.title()),
        duration_days=duration_days,
        price=price,
        currency=data.currency or "EUR",
        valid_from=data.valid_from or datetime.utcnow(),
        valid_until=data.valid_until or (datetime.utcnow() + timedelta(days=data.expiry_days or 90)),
        notes=data.notes,
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    logger.info(f"Code généré avec succès: {generated_code}")
    
    return {
        "success": True,
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "price": float(code.price),
        "currency": code.currency,
        "duration_days": code.duration_days,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None,
        "created_at": code.created_at.isoformat() if code.created_at else None,
        "status": code.status.value if hasattr(code.status, 'value') else code.status
    }


@router.get("/admin/list", response_model=Dict[str, Any])
async def list_subscription_codes(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Liste tous les codes d'abonnement générés.
    """
    query = db.query(SubscriptionCode)
    
    if status:
        try:
            status_enum = SubscriptionCodeStatus(status)
            query = query.filter(SubscriptionCode.status == status_enum)
        except ValueError:
            pass
    
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
                "created_by": code.created_by_user.email if code.created_by_user else None
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
        "days_remaining": code.days_remaining()
    }


@router.post("/admin/manual-activate/{user_id}")
async def manual_activate_user(
    user_id: UUID,
    plan_type: str = Query(..., description="Type de plan"),
    duration_days: int = Query(30, ge=1, le=3650, description="Durée en jours"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_super_admin_user),
) -> Any:
    """
    Activation manuelle d'un utilisateur (paiement cash sans code).
    Le super admin peut activer directement un compte.
    """
    logger.info(f"Activation manuelle de l'utilisateur {user_id} par {current_user.email}")
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "user_not_found",
                "message": "Utilisateur non trouvé."
            }
        )
    
    try:
        from app.services.subscription_service import create_user_subscription
        
        end_date = datetime.utcnow() + timedelta(days=duration_days)
        
        subscription = create_user_subscription(
            db=db,
            user_id=user.id,
            tenant_id=user.tenant_id,
            plan_type=plan_type
        )
        
        # Mettre à jour les dates si nécessaire
        subscription.end_date = end_date
        subscription.status = "active"
        
        # Ajouter les métadonnées
        config = subscription.config or {}
        config.update({
            "activated_by": str(current_user.id),
            "activated_by_email": current_user.email,
            "activation_type": "manual_cash",
            "notes": "Activation manuelle par super admin",
            "activated_at": datetime.utcnow().isoformat()
        })
        subscription.config = config
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Utilisateur {user.email} activé manuellement pour {duration_days} jours.",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "tenant_id": str(user.tenant_id) if user.tenant_id else None
            },
            "subscription": {
                "plan": subscription.plan_type,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": (subscription.end_date - datetime.utcnow()).days if subscription.end_date else 0
            },
            "activated_by": current_user.email,
            "activated_at": datetime.utcnow().isoformat()
        }
        
    except Exception as exc:
        logger.error(f"Erreur activation manuelle: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": f"Erreur lors de l'activation manuelle: {str(exc)}"
            }
        )


# =============================================================================
# ENDPOINTS UTILISATEUR - ACTIVATION AVEC CODE
# =============================================================================

@router.post("/activate", response_model=Dict[str, Any])
async def activate_with_code(
    data: ActivateSubscriptionCode,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Active un abonnement avec un code (paiement cash).
    """
    logger.info(f"Tentative d'activation avec code par {current_user.email}")
    
    # Vérifier si l'utilisateur a déjà un abonnement actif
    from app.services.subscription_service import check_user_subscription
    current_sub = check_user_subscription(db, str(current_user.id))
    
    if current_sub.get("is_active", False) and not data.force:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "already_active",
                "message": "Vous avez déjà un abonnement actif.",
                "current_plan": current_sub.get("plan")
            }
        )
    
    # Nettoyer le code
    clean_code = data.code.strip().upper().replace('-', '').replace(' ', '')
    
    # Chercher le code avec différentes variations
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
    
    # Vérifier validité du code
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
        # Activer l'abonnement
        subscription = activate_subscription_with_code(
            db=db,
            user=current_user,
            code=code
        )
        
        # Marquer le code comme utilisé
        code.status = SubscriptionCodeStatus.ACTIVATED
        code.activated_by_user_id = current_user.id
        code.activated_at = datetime.utcnow()
        
        db.commit()
        
        return {
            "success": True,
            "message": "Abonnement activé avec succès !",
            "subscription": {
                "plan": subscription.plan_type,
                "plan_name": subscription.plan_name,
                "start_date": subscription.start_date.isoformat() if subscription.start_date else None,
                "end_date": subscription.end_date.isoformat() if subscription.end_date else None,
                "days_remaining": (subscription.end_date - datetime.utcnow()).days if subscription.end_date else 0
            },
            "code": {
                "code": code.code,
                "plan": code.plan_type,
                "duration_days": code.duration_days
            }
        }
        
    except Exception as exc:
        logger.error(f"Erreur activation code: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "activation_failed",
                "message": "Erreur lors de l'activation. Veuillez contacter le support."
            }
        )


# =============================================================================
# ENDPOINTS DE VALIDATION (GET ET POST)
# =============================================================================

@router.get("/validate", response_model=Dict[str, Any])
@router.post("/validate", response_model=Dict[str, Any])
async def validate_code(
    code: str = Query(..., description="Code à valider"),
    db: Session = Depends(get_db),
    # Pour POST, on peut aussi recevoir le code dans le body
    data: Optional[ActivateSubscriptionCode] = None,
) -> Any:
    """
    Valide un code sans l'activer.
    Accepte GET (avec query param) ou POST (avec body).
    """
    # Si c'est une requête POST avec body, utiliser data.code
    code_to_validate = data.code if data else code
    
    logger.info(f"Validation de code: {code_to_validate}")
    result = validate_code_logic(db, code_to_validate)
    
    # Ajouter des informations supplémentaires pour le débogage si nécessaire
    if not result["valid"]:
        logger.warning(f"Code invalide: {code_to_validate} - {result['message']}")
    
    return result


# Endpoint de débogage pour tester la génération
@router.post("/debug/generate-test", include_in_schema=False)
async def debug_generate_test_code(
    plan_type: str = "pro",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Endpoint de débogage pour générer un code de test.
    (Non documenté, accessible seulement en développement)
    """
    if plan_type not in PLAN_CONFIG:
        plan_type = "pro"
    
    # Générer un code
    raw_code = generate_unique_code(8)
    formatted_code = format_code_with_dashes(raw_code)
    
    plan_config = PLAN_CONFIG[plan_type]
    
    code = SubscriptionCode(
        code=formatted_code,
        plan_type=plan_type,
        plan_name=plan_config.get("name", plan_type),
        duration_days=30,
        price=plan_config.get("price_monthly", 0),
        currency="EUR",
        valid_from=datetime.utcnow(),
        valid_until=datetime.utcnow() + timedelta(days=90),
        created_by_user_id=current_user.id,
        status=SubscriptionCodeStatus.PENDING,
        notes="Code de test généré automatiquement"
    )
    
    db.add(code)
    db.commit()
    db.refresh(code)
    
    return {
        "success": True,
        "message": "Code de d'abonnement généré",
        "code": code.code,
        "plan_type": code.plan_type,
        "plan_name": code.plan_name,
        "valid_until": code.valid_until.isoformat() if code.valid_until else None
    }