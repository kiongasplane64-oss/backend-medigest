# app/api/v1/auth.py
from datetime import datetime, timedelta
import logging
import random
import re
import uuid
from typing import Optional
import os
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, EmailStr, field_validator, model_validator
from sqlalchemy.orm import Session
from jose import jwt  

from app.api.deps import get_current_user
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    get_password_hash
)
from app.db.session import get_db
from app.models.pharmacy import Pharmacy
from app.models.tenant import Tenant
from app.models.user import User
from app.models.user_pharmacy import UserPharmacy
from app.models.payment import Payment
from app.services.notification_service import send_sms, send_whatsapp, send_sms_with_fallback
from app.services.subscription_service import check_subscription_status
from prometheus_client import Counter, Histogram
from app.core.config import settings 

login_attempts = Counter('login_attempts_total', 'Total login attempts')
login_duration = Histogram('login_duration_seconds', 'Login duration')

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
logger = logging.getLogger(__name__)

# Constantes
OTP_EXPIRATION_MIN = 5
RESET_EXPIRATION_MIN = 10
MAX_LOGIN_ATTEMPTS = 5
LOCK_MIN = 15

# Cache pour rate limiting
_rate_limiter_cache = {}


# =========================
# MODÈLES DE DONNÉES (Pydantic Schemas)
# =========================
class TenantRegisterSchema(BaseModel):
    email: EmailStr
    password: str
    confirm_password: Optional[str] = None
    nom_complet: str
    nom_pharmacie: str
    ville: str
    telephone: str
    type_pharmacie: str = None
    pays: str = "RDC"
    plan: Optional[str] = None
    plan_name: Optional[str] = None

    @field_validator("password")
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Mot de passe trop court (8 caractères minimum)")
        if len(v.encode('utf-8')) > 72:
            raise ValueError("Mot de passe trop long (72 caractères maximum)")
        if not any(c.isupper() for c in v):
            raise ValueError("Au moins une majuscule requise")
        if not any(c.islower() for c in v):
            raise ValueError("Au moins une minuscule requise")
        if not any(c.isdigit() for c in v):
            raise ValueError("Au moins un chiffre requis")
        return v
    
    @field_validator("telephone")
    def validate_phone(cls, v: str) -> str:
        v = re.sub(r'\D', '', v)
        
        if len(v) < 9:
            raise ValueError("Numéro de téléphone invalide (minimum 9 chiffres)")
        
        if len(v) == 9:
            return v
        elif len(v) == 11 and v.startswith('243'):
            return v
        elif len(v) == 12 and v.startswith('243'):
            return v[1:] if v[0] == '0' else v
        else:
            return v

    @model_validator(mode="after")
    def check_passwords(cls, model):
        if model.confirm_password and model.password != model.confirm_password:
            raise ValueError("Les mots de passe ne correspondent pas")
        return model

class LoginWithCodeSchema(BaseModel):
    email: EmailStr
    password: str
    verification_code: str

class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class ResetRequestSchema(BaseModel):
    email: EmailStr


class ResetConfirmSchema(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class VerifySMSSchema(BaseModel):
    email: EmailStr
    code: str


class ResendSMSSchema(BaseModel):
    email: EmailStr


class ExistingPhoneVerificationRequest(BaseModel):
    phone: str
    email: Optional[str] = None  # Pour identifier l'utilisateur

class ExistingPhoneVerificationConfirm(BaseModel):
    phone: str
    code: str
    action: str = "continue"  # "continue" ou "cancel"

class PhoneExistsResponse(BaseModel):
    exists: bool
    is_active: bool = False
    email_hint: Optional[str] = None  # Premier 3 lettres + @...com
    verification_required: bool = False
    verification_sent: bool = False

class SuperAdminSetup(BaseModel):
    email: EmailStr
    password: str
    nom_complet: str
    setup_key: str  # La clé secrète de déploiement


# =========================
# FONCTIONS UTILITAIRES
# =========================
def format_phone_for_twilio(phone: str) -> str:
    """Formate un numéro de téléphone pour Twilio (format E.164)"""
    if not phone:
        return phone
    
    phone = re.sub(r'\D', '', phone)
    
    if not phone:
        return phone
    
    if phone.startswith('0'):
        phone = phone[1:]
    
    if len(phone) == 9:
        return f"+243{phone}"
    elif len(phone) == 11 and phone.startswith('243'):
        return f"+{phone}"
    elif phone.startswith('+'):
        return phone
    else:
        return f"+{phone}"


def rate_limit_check(key: str, max_attempts: int = 5, window_seconds: int = 300) -> bool:
    """Vérifie si une clé a dépassé la limite de tentatives"""
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=window_seconds)
    
    if key in _rate_limiter_cache:
        _rate_limiter_cache[key] = [
            timestamp for timestamp in _rate_limiter_cache[key]
            if timestamp > window_start
        ]
    
    attempts = _rate_limiter_cache.get(key, [])
    if len(attempts) >= max_attempts:
        logger.warning(f"Rate limit atteint pour {key}")
        return False
    
    attempts.append(now)
    _rate_limiter_cache[key] = attempts[-max_attempts:]
    return True


def generate_otp() -> str:
    """Génère un code OTP à 6 chiffres"""
    return str(random.randint(100000, 999999))


def generate_tenant_code(nom_pharmacie: str) -> str:
    """Génère un code unique pour un tenant"""
    prefix = nom_pharmacie[:3].upper().replace(' ', '')
    if len(prefix) < 3:
        prefix = prefix + 'PH'
    random_suffix = str(random.randint(100, 999))
    return f"{prefix}{random_suffix}"


def generate_slug(nom_pharmacie: str) -> str:
    """Génère un slug à partir du nom de la pharmacie"""
    slug = nom_pharmacie.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug.strip('-')

def generate_unique_slug(nom_pharmacie: str, db: Session) -> str:
    """Génère un slug unique à partir du nom de la pharmacie"""
    base_slug = generate_slug(nom_pharmacie)
    slug = base_slug
    counter = 1
    
    # Vérifier si le slug existe déjà
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
        if counter > 100:  # Limite de sécurité
            # Générer un slug avec UUID si trop de collisions
            import uuid
            slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"
            break
    
    return slug

def generate_unique_tenant_code(nom_pharmacie: str, db: Session) -> str:
    """Génère un code unique pour un tenant avec vérification"""
    prefix = nom_pharmacie[:3].upper().replace(' ', '')
    if len(prefix) < 3:
        prefix = prefix + 'PH'
    
    counter = 1
    while True:
        random_suffix = str(random.randint(100, 999))
        tenant_code = f"{prefix}{random_suffix}"
        
        # Vérifier l'unicité
        if not db.query(Tenant).filter(Tenant.tenant_code == tenant_code).first():
            return tenant_code
        
        counter += 1
        if counter > 10:  # Après 10 tentatives, utiliser un UUID
            import uuid
            return f"PH{str(uuid.uuid4())[:8].upper()}"


def is_subscription_active(db: Session, tenant_id: str) -> bool:
    """Vérifie si l'abonnement est actif pour un tenant donné"""
    try:
        return check_subscription_status(db, tenant_id)
    except Exception as e:
        logger.error(f"Erreur lors de la vérification de l'abonnement: {e}")
        return False


# =========================
# ENDPOINTS D'AUTHENTIFICATION
# =========================
@router.post("/tenants/register", status_code=201)
def register_tenant(data: TenantRegisterSchema, db: Session = Depends(get_db)):
    """Inscription d'un nouveau tenant (pharmacie)"""
    
    # 1. VÉRIFICATIONS PRÉLIMINAIRES RENFORCÉES
    # ------------------------------
    # Vérifier l'email
    existing_user = db.query(User).filter(User.email == data.email.lower()).first()
    if existing_user:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "email_already_used",
                "message": "Cet email est déjà utilisé",
                "suggestion": "Utilisez un autre email ou connectez-vous si c'est votre compte"
            }
        )
    
    # Vérifier le nom de pharmacie
    existing_pharmacy_name = db.query(Tenant).filter(
        Tenant.nom_pharmacie.ilike(data.nom_pharmacie.strip())
    ).first()
    
    if existing_pharmacy_name:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pharmacy_name_exists",
                "message": f"Le nom de pharmacie '{data.nom_pharmacie}' existe déjà",
                "suggestion": "Ajoutez votre localisation au nom (ex: 'Ma Pharmacie - Kinshasa')"
            }
        )
    
    # Vérifier le téléphone
    existing_phone = db.query(Tenant).filter(
        Tenant.telephone_principal == data.telephone
    ).first()
    
    if existing_phone:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "phone_already_used",
                "message": "Ce numéro de téléphone est déjà utilisé",
                "suggestion": "Utilisez un autre numéro ou contactez le support"
            }
        )

    if len(data.password.encode('utf-8')) > 72:
        raise HTTPException(400, "Mot de passe trop long (max 72 bytes pour bcrypt).")

    # 2. GESTION DU PLAN D'ABONNEMENT
    # --------------------------------
    # Utiliser le plan fourni ou "professional" par défaut
    if not data.plan:
        raise HTTPException(400, "Plan d'abonnement requis")

    plan = data.plan
    plan_name = data.plan_name if data.plan_name else plan.capitalize()

    # Définir les limites selon le plan
    plan_limits = {
        "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
        "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
        "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
    }

    if plan not in plan_limits:
        raise HTTPException(400, f"Plan invalide. Options: {', '.join(plan_limits.keys())}")

    limits = plan_limits.get(plan)
    
    # 3. GÉNÉRATION DES IDENTIFIANTS UNIQUES
    # --------------------------------------
    tenant_code = generate_unique_tenant_code(data.nom_pharmacie, db)
    slug = generate_unique_slug(data.nom_pharmacie, db)
    pharmacy_code = f"{tenant_code}001"

    # 4. CRÉATION DU TENANT (PHARMACIE)
    # ----------------------------------
    try:
        tenant = Tenant(
            # Identifiants
            tenant_code=tenant_code,
            slug=slug,
            
            # Informations générales
            nom_pharmacie=data.nom_pharmacie,
            nom_commercial=data.nom_pharmacie,
            ville=data.ville,
            pays=data.pays,
            
            # Contacts
            telephone_principal=data.telephone,
            email_admin=data.email.lower(),
            nom_proprietaire=data.nom_complet,
            
            # Type et statut
            type_pharmacie=data.type_pharmacie,
            status="trial",
            
            # Plan et limites
            max_users=limits["max_users"],
            max_products=limits["max_products"],
            current_plan=plan,
            max_pharmacies=limits["max_pharmacies"],
            
            # Période d'essai
            trial_start_date=datetime.utcnow(),
            trial_end_date=datetime.utcnow() + timedelta(days=14),
            
            # Configuration
            config={"plan_name": plan_name}
        )
        db.add(tenant)
        db.flush()  # Pour obtenir l'ID

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création tenant: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "tenant_creation_failed",
                "message": "Erreur lors de la création de votre pharmacie",
                "suggestion": "Réessayez dans quelques instants ou contactez le support"
            }
        )

    # 5. GÉNÉRATION DU CODE DE VÉRIFICATION
    # --------------------------------------
    otp = generate_otp()

    # 6. CRÉATION DE L'UTILISATEUR ADMIN
    # -----------------------------------
    try:
        pw = data.password
        logger.info("REGISTER pw chars=%s bytes=%s", len(pw), len(pw.encode("utf-8")))
        hashed_password = hash_password(data.password)
        admin = User(
            tenant_id=tenant.id,
            nom_complet=data.nom_complet,
            email=data.email.lower(),
            password_hash=hashed_password,
            role="admin",
            actif=False,
            telephone=data.telephone,
            sms_code=otp,
            sms_expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRATION_MIN),
            login_attempts=0,
            sms_verify_attempts=0,
        )
        db.add(admin)
        db.flush()  # Pour obtenir l'ID
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création admin: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "admin_creation_failed",
                "message": "Erreur lors de la création du compte administrateur",
                "suggestion": "Vérifiez vos informations et réessayez"
            }
        )

    # 7. CRÉATION DE LA PHARMACIE PRINCIPALE
    # ---------------------------------------
    try:
        # Générer un numéro de licence par défaut
        license_number = f"LIC-{tenant_code}-{datetime.utcnow().strftime('%Y%m')}"
        
        pharmacy = Pharmacy(
            tenant_id=tenant.id,
            name=data.nom_pharmacie,
            address=data.ville,
            city=data.ville,
            phone=data.telephone,
            email=data.email.lower(),
            is_active=True,
            is_main=True,
            pharmacy_code=pharmacy_code,
            license_number=license_number,  
            country=data.pays,
            config={
                "require_prescription": True,
                "enable_expiry_alerts": True,
                "low_stock_threshold": 10,
                "enable_barcode": True,
                "tax_rate": 18.0,
                "currency": "XOF",
                "language": "fr",
                "date_format": "dd/MM/yyyy",
                "decimal_precision": 2
            }
        )
        db.add(pharmacy)
        db.flush()  # Pour obtenir l'ID
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création pharmacie: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "pharmacy_creation_failed",
                "message": "Erreur lors de la création de la pharmacie",
                "suggestion": "Contactez le support technique"
            }
        )

    # 8. ASSOCIATION ADMIN-PHARMACIE
    # -------------------------------
    try:
        association = UserPharmacy(
            user_id=admin.id,
            pharmacy_id=pharmacy.id,
            is_primary=True,
            role_in_pharmacy="admin"
        )
        db.add(association)
        
        # 9. VALIDATION DE LA TRANSACTION
        # --------------------------------
        db.commit()
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur association ou commit: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "database_error",
                "message": "Erreur lors de l'enregistrement",
                "suggestion": "Veuillez réessayer dans quelques instants"
            }
        )

    # 10. ENVOI DU SMS DE CONFIRMATION
    # ---------------------------------
    sms_sent = False
    whatsapp_sent = False
    try:
        formatted_phone = format_phone_for_twilio(data.telephone)
        logger.info(f"Envoi SMS à {formatted_phone} - Plan: {plan}")
        
        # Utiliser send_sms_with_fallback pour meilleure fiabilité
        result = send_sms_with_fallback(formatted_phone, f"Code de confirmation : {otp}")
        
        sms_sent = result.get('success', False)
        method = result.get('method', 'none')
        
        if sms_sent:
            logger.info(f"SMS envoyé via {method} avec succès")
        else:
            logger.error(f"Échec envoi SMS: {result.get('error')}")
            
    except Exception as e:
        logger.error(f"Erreur envoi SMS: {e}")
        # On continue même si le SMS échoue

    # 11. RÉPONSE AU CLIENT
    # ----------------------
    return {
        "message": "Compte créé avec succès. Confirmation SMS requise.",
        "tenant_id": str(tenant.id),
        "user_id": str(admin.id),
        "tenant_code": tenant_code,
        "pharmacy_id": str(pharmacy.id),
        "verification_code": otp if not sms_sent else None,
        "sms_sent": sms_sent,
        "plan": plan,
        "plan_name": plan_name,
        "trial_end_date": tenant.trial_end_date.isoformat(),
        "suggestions": {
            "save_credentials": "Conservez vos identifiants en sécurité",
            "complete_profile": "Complétez votre profil après activation",
            "trial_period": f"Profitez de vos {14} jours d'essai gratuit"
        }
    }

@router.post("/test-sms")
def test_sms(phone: str, db: Session = Depends(get_db)):
    """Test de l'envoi SMS"""
    try:
        formatted_phone = format_phone_for_twilio(phone)
        result = send_sms_with_fallback(
            formatted_phone, 
            "Test SMS MEDIGEST - " + datetime.utcnow().strftime("%H:%M")
        )
        
        return {
            "success": result.get('success', False),
            "method": result.get('method'),
            "error": result.get('error'),
            "phone": formatted_phone
        }
    except Exception as e:
        raise HTTPException(500, f"Erreur test SMS: {e}")
    
@router.get("/notification-config")
def get_notification_config():
    """Retourne la configuration des notifications"""
    from app.services.notification_service import test_twilio_connection
    
    twilio_test = test_twilio_connection()
    
    return {
        "twilio": twilio_test,
        "sms_enabled": True,
        "whatsapp_enabled": True,
        "timestamp": datetime.utcnow().isoformat()
    }

@router.post("/tenants/check-availability")
def check_availability(
    email: Optional[str] = None,
    pharmacy_name: Optional[str] = None,
    phone: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Vérifie la disponibilité des identifiants avant inscription"""
    results = {
        "available": True,
        "checks": [],
        "suggestions": []
    }
    
    if email:
        existing_user = db.query(User).filter(User.email == email.lower()).first()
        if existing_user:
            results["available"] = False
            results["checks"].append({
                "field": "email",
                "available": False,
                "message": "Email déjà utilisé"
            })
            results["suggestions"].append({
                "field": "email",
                "message": "Utilisez un autre email ou connectez-vous"
            })
        else:
            results["checks"].append({
                "field": "email",
                "available": True,
                "message": "Email disponible"
            })
    
    if pharmacy_name:
        existing_pharmacy = db.query(Tenant).filter(
            Tenant.nom_pharmacie.ilike(pharmacy_name.strip())
        ).first()
        
        if existing_pharmacy:
            results["available"] = False
            results["checks"].append({
                "field": "pharmacy_name",
                "available": False,
                "message": f"Nom '{pharmacy_name}' existe déjà"
            })
            
            # Suggestions alternatives
            suggestions = [
                f"{pharmacy_name} - {existing_pharmacy.ville}",
                f"{pharmacy_name} Centre",
                f"{pharmacy_name} Principal",
                f"Pharmacie {pharmacy_name}"
            ]
            
            results["suggestions"].append({
                "field": "pharmacy_name",
                "message": "Nom non disponible",
                "alternatives": suggestions[:3]
            })
        else:
            results["checks"].append({
                "field": "pharmacy_name",
                "available": True,
                "message": "Nom disponible"
            })
    
    if phone:
        existing_phone = db.query(Tenant).filter(Tenant.telephone_principal == phone).first()
        if existing_phone:
            results["available"] = False
            results["checks"].append({
                "field": "phone",
                "available": False,
                "message": "Téléphone déjà utilisé"
            })
        else:
            results["checks"].append({
                "field": "phone",
                "available": True,
                "message": "Téléphone disponible"
            })
    
    return results

@router.post("/verify-sms")
def verify_sms(data: VerifySMSSchema, db: Session = Depends(get_db)):
    """Vérification du code SMS et activation du compte"""
    email = data.email.lower()
    code = data.code.strip()
    
    # 1. Validations de base
    if not email or not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email et code requis")
    
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code invalide (6 chiffres requis)")
    
    # 2. Rate Limiting
    if not rate_limit_check(f"sms_verify_{email}", max_attempts=5, window_seconds=300):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Trop de tentatives. Réessayez dans 5 minutes."
        )
    
    try:
        # 3. Recherche de l'utilisateur (sans le filtre actif=False pour mieux gérer les erreurs)
        user = db.query(User).filter(User.email == email).first()
        
        if not user:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur non trouvé")

        # 4. Gestion du compte déjà activé
        if user.actif:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, 
                "Compte déjà activé, veuillez vous connecter"
            )

        # 5. Vérification du verrouillage (Brute force)
        if user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Compte bloqué. Réessayez dans {remaining} minutes."
            )
        
        # 6. Vérification du code OTP
        if not user.sms_code or user.sms_code != code:
            user.sms_verify_attempts = getattr(user, 'sms_verify_attempts', 0) + 1
            
            if user.sms_verify_attempts >= 3:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                logger.warning(f"Compte bloqué après 3 échecs SMS: {email}")
            
            db.commit()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Code invalide")
        
        # 7. Vérification de l'expiration
        if not user.sms_expires_at or user.sms_expires_at < datetime.utcnow():
            # Génération d'un nouveau code si expiré
            new_code = generate_otp()
            user.sms_code = new_code
            user.sms_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRATION_MIN)
            user.sms_verify_attempts = 0
            db.commit()
            
            try:
                formatted_phone = format_phone_for_twilio(user.telephone)
                send_sms(formatted_phone, f"Nouveau code: {new_code}")
            except Exception as sms_error:
                logger.error(f"Erreur envoi nouveau SMS: {sms_error}")
            
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Code expiré. Un nouveau code a été envoyé."
            )

        # 8. ACTIVATION (Tout est OK)
        user.actif = True
        user.sms_code = None
        user.sms_expires_at = None
        user.sms_verify_attempts = 0
        user.locked_until = None
        user.activated_at = datetime.utcnow()
        
        # Activation du Tenant associé
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        if not tenant:
            logger.error(f"Tenant non trouvé pour utilisateur: {user.id}")
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Erreur système : Tenant introuvable")

        tenant.status = "active"
        tenant.activated_at = datetime.utcnow()
        if not tenant.trial_end_date:
            tenant.trial_end_date = datetime.utcnow() + timedelta(days=14)
        
        # Récupération de la pharmacie principale
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == tenant.id,
            Pharmacy.is_main == True
        ).first()
        
        # Création du token d'accès final
        token = create_access_token({
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "email": user.email,
            "activated": True
        })
        
        db.commit()
        logger.info(f"Compte activé avec succès: {email}")

        # 9. Construction de la réponse
        response_data = {
            "message": "Compte activé avec succès",
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "nom_complet": user.nom_complet,
                "role": user.role,
                "activated": True
            },
            "tenant": {
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "nom_pharmacie": tenant.nom_pharmacie,
                "status": tenant.status
            }
        }
        
        if pharmacy:
            response_data["pharmacy"] = {"id": str(pharmacy.id), "name": pharmacy.name}
        
        return response_data
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur critique verify_sms: {str(e)}", exc_info=True)
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Une erreur interne est survenue")

@router.post("/resend-sms")
def resend_sms_code(data: ResendSMSSchema, db: Session = Depends(get_db)):
    """Renvoie un nouveau code SMS de vérification"""
    email = data.email.lower()
    
    if not rate_limit_check(f"resend_sms_{email}", max_attempts=3, window_seconds=3600):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Trop de demandes. Réessayez dans 1 heure."
        )
    
    try:
        user = db.query(User).filter(
            User.email == email,
            User.actif == False
        ).first()
        
        if not user:
            return {
                "message": "Si votre compte existe et n'est pas activé, un nouveau code sera envoyé."
            }
        
        if user.locked_until and user.locked_until > datetime.utcnow():
            remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Compte bloqué. Réessayez dans {remaining} minutes."
            )
        
        new_code = generate_otp()
        user.sms_code = new_code
        user.sms_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRATION_MIN)
        user.sms_verify_attempts = 0
        
        db.commit()
        
        try:
            formatted_phone = format_phone_for_twilio(user.telephone)
            send_sms(formatted_phone, f"Nouveau code: {new_code}")
            sms_sent = True
        except Exception as e:
            logger.error(f"Erreur envoi SMS: {e}")
            sms_sent = False
        
        return {
            "message": "Nouveau code envoyé" if sms_sent else "Code généré mais SMS échoué",
            "sms_sent": sms_sent,
            "expires_in": OTP_EXPIRATION_MIN
        }
        
    except Exception as e:
        logger.error(f"Erreur renvoi SMS: {e}")
        db.rollback()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Erreur lors de l'envoi du code"
        )

@router.post("/check-phone-exists")
async def check_phone_exists(
    phone: str,
    db: Session = Depends(get_db)
):
    """
    Vérifie si un numéro existe déjà et propose une vérification
    """
    # Nettoyer le numéro
    phone_clean = re.sub(r'\D', '', phone)
    
    # Chercher dans les tenants (pharmacies)
    tenant = db.query(Tenant).filter(
        Tenant.telephone_principal == phone_clean
    ).first()
    
    if not tenant:
        # Chercher dans les utilisateurs
        user = db.query(User).filter(User.telephone == phone_clean).first()
        if user:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    
    if not tenant:
        return {
            "exists": False,
            "message": "Numéro disponible"
        }
    
    # Si le numéro existe, masquer l'email pour la confidentialité
    email = tenant.email_admin if tenant else None
    if email:
        # Masquer l'email (ex: "use***@gmail.com")
        email_parts = email.split('@')
        if len(email_parts) == 2:
            masked_email = f"{email_parts[0][:3]}***@{email_parts[1]}"
        else:
            masked_email = "utilisateur@..."
    else:
        masked_email = None
    
    return {
        "exists": True,
        "is_active": tenant.status == "active" if tenant else False,
        "email_hint": masked_email,
        "message": f"Ce numéro est associé à un compte existant ({masked_email})",
        "verification_required": True,
        "suggestions": [
            "Si c'est votre compte, nous allons vous envoyer un code de vérification",
            "Si ce n'est pas votre compte, utilisez un autre numéro"
        ]
    }

@router.post("/verify-existing-phone/request")
async def request_existing_phone_verification(
    data: ExistingPhoneVerificationRequest,
    db: Session = Depends(get_db)
):
    """
    Envoie un code de vérification à un numéro existant
    """
    phone_clean = re.sub(r'\D', '', data.phone)
    
    # Chercher le compte associé
    tenant = db.query(Tenant).filter(
        Tenant.telephone_principal == phone_clean
    ).first()
    
    if not tenant:
        user = db.query(User).filter(User.telephone == phone_clean).first()
        if user:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    
    if not tenant:
        raise HTTPException(404, "Numéro non trouvé")
    
    # Générer un code temporaire (valide 10 minutes)
    verification_code = generate_otp()
    
    # Stocker dans la table User temporairement (ou créer une table dédiée)
    user = db.query(User).filter(
        User.tenant_id == tenant.id,
        User.role == "admin"
    ).first()
    
    if user:
        user.temp_verification_code = verification_code
        user.temp_verification_expires = datetime.utcnow() + timedelta(minutes=10)
        user.temp_phone_to_verify = phone_clean
    else:
        # Créer un enregistrement temporaire dans User
        user = User(
            email=f"temp_verification_{phone_clean}@temp.com",
            telephone=phone_clean,
            temp_verification_code=verification_code,
            temp_verification_expires=datetime.utcnow() + timedelta(minutes=10),
            temp_phone_to_verify=phone_clean,
            tenant_id=tenant.id
        )
        db.add(user)
    
    db.commit()
    
    # Envoyer le SMS
    formatted_phone = format_phone_for_twilio(phone_clean)
    message = (
        f"MEDIGEST - Vérification de numéro\n"
        f"Code: {verification_code}\n"
        f"Si vous n'êtes pas à l'origine de cette demande, ignorez ce message."
    )
    
    sms_result = send_sms_with_fallback(formatted_phone, message)
    
    return {
        "success": sms_result.get('success', False),
        "message": "Code de vérification envoyé" if sms_result.get('success') else "Erreur d'envoi",
        "method": sms_result.get('method'),
        "expires_in": 10,
        "phone": phone_clean,
        "email_hint": f"{tenant.email_admin[:3]}***@{tenant.email_admin.split('@')[1]}" if '@' in tenant.email_admin else None
    }

@router.post("/verify-existing-phone/confirm")
async def confirm_existing_phone_verification(
    data: ExistingPhoneVerificationConfirm,
    db: Session = Depends(get_db)
):
    """
    Vérifie le code et autorise la suite de l'inscription
    """
    phone_clean = re.sub(r'\D', '', data.phone)
    
    # Chercher l'enregistrement de vérification
    user = db.query(User).filter(
        User.temp_phone_to_verify == phone_clean,
        User.temp_verification_code == data.code.strip()
    ).first()
    
    if not user:
        raise HTTPException(400, "Code invalide")
    
    if not user.temp_verification_expires or user.temp_verification_expires < datetime.utcnow():
        raise HTTPException(400, "Code expiré")
    
    if data.action == "continue":
        # Vérification réussie - créer un token temporaire pour l'inscription
        verification_token = create_access_token(
            data={
                "verified_phone": phone_clean,
                "action": "continue_registration",
                "exp": datetime.utcnow() + timedelta(minutes=30)
            },
            expires_delta=timedelta(minutes=30)
        )
        
        # Nettoyer les données temporaires
        user.temp_verification_code = None
        user.temp_verification_expires = None
        user.temp_phone_to_verify = None
        db.commit()
        
        return {
            "success": True,
            "message": "Numéro vérifié avec succès",
            "verification_token": verification_token,
            "verified_phone": phone_clean,
            "expires_in": 30
        }
    else:
        # Annulation
        user.temp_verification_code = None
        user.temp_verification_expires = None
        user.temp_phone_to_verify = None
        db.commit()
        
        return {
            "success": True,
            "message": "Vérification annulée"
        }

@router.post("/tenants/register-with-verified-phone")
def register_with_verified_phone(
    data: TenantRegisterSchema,
    verification_token: str = Query(...),
    db: Session = Depends(get_db)
):
    """
    Inscription avec numéro déjà vérifié
    """
    # Vérifier le token
    try:
        payload = jwt.decode(
            verification_token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        
        if payload.get("action") != "continue_registration":
            raise HTTPException(400, "Token invalide")
        
        verified_phone = payload.get("verified_phone")
        if verified_phone != data.telephone:
            raise HTTPException(400, "Le numéro ne correspond pas")
            
    except jwt.ExpiredSignatureError:
        raise HTTPException(400, "Token expiré")
    except jwt.InvalidTokenError:
        raise HTTPException(400, "Token invalide")
    
    # Vérifier que le numéro n'est pas déjà utilisé (double sécurité)
    phone_clean = re.sub(r'\D', '', data.telephone)
    existing = db.query(Tenant).filter(
        Tenant.telephone_principal == phone_clean
    ).first()
    
    if existing:
        # Vérifier si c'est le même compte (email différent)
        if existing.email_admin != data.email.lower():
            raise HTTPException(
                409,
                detail={
                    "error": "phone_verified_but_email_differs",
                    "message": "Ce numéro appartient à un autre compte",
                    "suggestion": "Utilisez un autre numéro ou connectez-vous avec l'email associé"
                }
            )

@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    """Connexion utilisateur"""
    logger.info(f"Tentative de login pour: {data.email}")
    
    user = db.query(User).filter(User.email == data.email.lower()).first()
    
    if not user:
        logger.warning(f"Utilisateur non trouvé: {data.email}")
        raise HTTPException(401, "Identifiants invalides")
    
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        raise HTTPException(403, f"Compte temporairement bloqué. Réessayez dans {remaining} minutes.")
    
    if not verify_password(data.password, user.password_hash):
        user.login_attempts += 1
        if user.login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MIN)
            user.login_attempts = 0
        db.commit()
        raise HTTPException(401, "Identifiants invalides")
    
    # MODIFICATION : Si compte non activé, proposer la vérification
    if not user.actif:
        # Vérifier s'il y a un code en attente
        has_pending_code = bool(user.sms_code and user.sms_expires_at)
        
        # Si pas de code ou code expiré, en envoyer un nouveau
        if not has_pending_code or (user.sms_expires_at and user.sms_expires_at < datetime.utcnow()):
            new_code = generate_otp()
            user.sms_code = new_code
            user.sms_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRATION_MIN)
            
            try:
                formatted_phone = format_phone_for_twilio(user.telephone)
                send_sms(formatted_phone, f"Code de vérification: {new_code}")
                sms_sent = True
                logger.info(f"Nouveau code envoyé à {data.email}")
            except Exception as e:
                logger.error(f"Erreur envoi SMS: {e}")
                sms_sent = False
            
            db.commit()
            
            raise HTTPException(
                403,
                {
                    "error": "account_not_activated",
                    "message": "Compte non activé. Un code de vérification a été envoyé.",
                    "requires_verification": True,
                    "email": data.email,
                    "sms_sent": sms_sent,
                    "verification_required": True
                }
            )
        else:
            # Il y a déjà un code valide
            raise HTTPException(
                403,
                {
                    "error": "account_not_activated",
                    "message": "Compte non activé. Entrez le code de vérification reçu par SMS.",
                    "requires_verification": True,
                    "email": data.email,
                    "verification_required": True,
                    "has_pending_code": True
                }
            )
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")

    subscription_active = is_subscription_active(db, str(user.tenant_id))
    
    # Récupérer les pharmacies accessibles pour l'utilisateur
    accessible_pharmacies = db.query(Pharmacy).join(
        UserPharmacy, UserPharmacy.pharmacy_id == Pharmacy.id
    ).filter(
        UserPharmacy.user_id == user.id,
        Pharmacy.is_active == True
    ).all()
    
    # Récupérer la pharmacie principale
    main_pharmacy = next((p for p in accessible_pharmacies if p.is_main), None)
    if not main_pharmacy and accessible_pharmacies:
        main_pharmacy = accessible_pharmacies[0]
    
    # Récupérer les pharmacies actives du tenant
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()
    
    # Réinitialiser les tentatives de login
    user.login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()

    # Créer le token
    token = create_access_token({
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "subscription_active": subscription_active,
        "pharmacy_id": str(main_pharmacy.id) if main_pharmacy else None
    })

    # Préparer la réponse avec le plan
    response_data = {
        "access_token": token,
        "token_type": "bearer",
        "subscription_active": subscription_active,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id),
            "actif": user.actif,
            "telephone": user.telephone
        },
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "nom_pharmacie": tenant.nom_pharmacie,
            "nom_commercial": tenant.nom_commercial,
            "ville": tenant.ville,
            "pays": tenant.pays,
            "email_admin": tenant.email_admin,
            "status": tenant.status,
            "current_plan": tenant.current_plan,  # Le plan choisi
            "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan,
            "max_users": tenant.max_users,
            "max_products": tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies,
            "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None
        },
        "pharmacies": []
    }
    
    # Ajouter les pharmacies
    for pharmacy in pharmacies:
        response_data["pharmacies"].append({
            "id": str(pharmacy.id),
            "name": pharmacy.name,
            "address": pharmacy.address,
            "city": pharmacy.city,
            "phone": pharmacy.phone,
            "email": pharmacy.email,
            "is_active": pharmacy.is_active,
            "is_main": pharmacy.is_main,
            "pharmacy_code": pharmacy.pharmacy_code,
            "created_at": pharmacy.created_at.isoformat() if pharmacy.created_at else None
        })
    
    # Ajouter la pharmacie active
    if main_pharmacy:
        response_data["current_pharmacy"] = {
            "id": str(main_pharmacy.id),
            "name": main_pharmacy.name,
            "address": main_pharmacy.address,
            "city": main_pharmacy.city,
            "phone": main_pharmacy.phone,
            "email": main_pharmacy.email,
            "is_main": main_pharmacy.is_main,
            "pharmacy_code": main_pharmacy.pharmacy_code
        }
    
    return response_data

@router.post("/login-with-code")
def login_with_verification_code(data: LoginWithCodeSchema, db: Session = Depends(get_db)):
    """
    Connexion avec vérification du code SMS pour les comptes non activés
    """
    from tkinter import messagebox
    
    logger.info(f"Tentative de login avec code pour: {data.email}")
    
    user = db.query(User).filter(User.email == data.email.lower()).first()
    
    if not user:
        logger.warning(f"Utilisateur non trouvé: {data.email}")
        raise HTTPException(401, "Identifiants invalides")
    
    # Vérifier le mot de passe d'abord
    if not verify_password(data.password, user.password_hash):
        user.login_attempts += 1
        if user.login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MIN)
            user.login_attempts = 0
        db.commit()
        raise HTTPException(401, "Identifiants invalides")
    
    # Si compte déjà activé, procéder normalement
    if user.actif:
        raise HTTPException(400, "Compte déjà activé. Utilisez le login normal.")
    
    # Vérifier le code de vérification
    if not user.sms_code or user.sms_code != data.verification_code:
        user.sms_verify_attempts += 1
        
        if user.sms_verify_attempts >= 3:
            user.locked_until = datetime.utcnow() + timedelta(minutes=15)
            logger.warning(f"Compte bloqué après 3 échecs code: {data.email}")
            db.commit()
            raise HTTPException(423, f"Compte bloqué. Réessayez dans 15 minutes.")
        
        db.commit()
        raise HTTPException(400, "Code de vérification invalide")
    
    # Vérifier si le code a expiré
    if not user.sms_expires_at or user.sms_expires_at < datetime.utcnow():
        # Générer et envoyer un nouveau code
        new_code = generate_otp()
        user.sms_code = new_code
        user.sms_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRATION_MIN)
        user.sms_verify_attempts = 0
        
        db.commit()
        
        # Envoyer le nouveau code
        try:
            formatted_phone = format_phone_for_twilio(user.telephone)
            send_sms(formatted_phone, f"Nouveau code: {new_code}")
            sms_sent = True
        except Exception as e:
            logger.error(f"Erreur envoi nouveau SMS: {e}")
            sms_sent = False
        
        raise HTTPException(
            400,
            {
                "error": "code_expired",
                "message": "Code expiré. Nouveau code envoyé.",
                "sms_sent": sms_sent,
                "resend_available": True
            }
        )
    
    # Activer le compte et procéder à la connexion
    user.actif = True
    user.sms_code = None
    user.sms_expires_at = None
    user.sms_verify_attempts = 0
    user.locked_until = None
    user.activated_at = datetime.utcnow()
    user.login_attempts = 0
    user.last_login = datetime.utcnow()
    
    # Activer le tenant
    tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    if tenant:
        if tenant.status == "trial":
            tenant.status = "active"
            tenant.activated_at = datetime.utcnow()
    
    # Récupérer la pharmacie principale
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_main == True
    ).first() if tenant else None
    
    db.commit()
    
    # Créer le token
    subscription_active = is_subscription_active(db, str(user.tenant_id))
    
    token = create_access_token({
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id),
        "role": user.role,
        "email": user.email,
        "subscription_active": subscription_active,
        "pharmacy_id": str(pharmacy.id) if pharmacy else None
    })
    
    logger.info(f"Compte activé et connecté: {data.email}")
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "subscription_active": subscription_active,
        "account_activated": True,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id),
            "actif": True,
            "telephone": user.telephone
        },
        "tenant": {
            "id": str(tenant.id) if tenant else None,
            "tenant_code": tenant.tenant_code if tenant else None,
            "nom_pharmacie": tenant.nom_pharmacie if tenant else None,
            "status": tenant.status if tenant else None
        }
    }

# =========================
# ENDPOINTS RÉINITIALISATION MOT DE PASSE
# =========================
@router.post("/password/reset/request")
def request_reset(data: ResetRequestSchema, db: Session = Depends(get_db)):
    """Demande de réinitialisation de mot de passe"""
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user:
        return {"message": "Si le compte existe, un code sera envoyé"}

    code = generate_otp()
    user.reset_code = code
    user.reset_expires = datetime.utcnow() + timedelta(minutes=RESET_EXPIRATION_MIN)
    db.commit()

    try:
        formatted_phone = format_phone_for_twilio(user.telephone)
        send_sms(formatted_phone, f"Code réinitialisation: {code}")
        send_whatsapp(formatted_phone, f"Code réinitialisation: {code}")
        sms_sent = True
    except Exception as e:
        logger.error(f"Erreur envoi SMS/WhatsApp: {e}")
        sms_sent = False

    return {"message": "Code envoyé", "sms_sent": sms_sent}


@router.post("/password/reset/confirm")
def confirm_reset(data: ResetConfirmSchema, db: Session = Depends(get_db)):
    """Confirmation de réinitialisation de mot de passe"""
    user = db.query(User).filter(User.email == data.email.lower()).first()

    if not user or user.reset_code != data.code:
        raise HTTPException(400, "Code invalide")

    if user.reset_expires < datetime.utcnow():
        raise HTTPException(400, "Code expiré")

    if len(data.new_password.encode('utf-8')) > 72:
        raise HTTPException(400, "Mot de passe trop long (72 caractères max)")

    user.password_hash = hash_password(data.new_password)
    user.reset_code = None
    user.reset_expires = None
    user.login_attempts = 0
    user.locked_until = None
    db.commit()

    return {"message": "Mot de passe modifié"}


# =========================
# ENDPOINTS DE VÉRIFICATION
# =========================
@router.get("/activation-status/{email}")
def check_activation_status(email: EmailStr, db: Session = Depends(get_db)):
    """Vérifie le statut d'activation d'un compte"""
    user = db.query(User).filter(User.email == email.lower()).first()
    
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Compte non trouvé")
    
    response = {
        "email": user.email,
        "activated": user.actif,
        "locked": bool(user.locked_until and user.locked_until > datetime.utcnow()),
        "has_pending_code": bool(user.sms_code and user.sms_expires_at),
    }
    
    if response["has_pending_code"]:
        expires_in = max(0, int((user.sms_expires_at - datetime.utcnow()).total_seconds() / 60))
        response["code_expires_in_minutes"] = expires_in
    
    if response["locked"]:
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        response["locked_until_minutes"] = remaining
    
    return response


# =========================
# ENDPOINTS INFORMATIONS TENANT/PHARMACIES
# =========================
@router.get("/pharmacy/limits/{tenant_id}")
def get_pharmacy_limits(tenant_id: str, db: Session = Depends(get_db)):
    """Récupère les limites de pharmacies pour un tenant"""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).count()
    
    return {
        "tenant_id": str(tenant.id),
        "current_plan": tenant.current_plan,
        "max_pharmacies": tenant.max_pharmacies,
        "active_pharmacies": active_pharmacies_count,
        "remaining_pharmacies": max(0, tenant.max_pharmacies - active_pharmacies_count),
        "can_create_more": active_pharmacies_count < tenant.max_pharmacies
    }


@router.get("/tenant-info/{tenant_id}")
def get_tenant_info(tenant_id: str, db: Session = Depends(get_db)):
    """Récupère les informations détaillées d'un tenant"""
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    return {
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "nom_pharmacie": tenant.nom_pharmacie,
            "nom_commercial": tenant.nom_commercial,
            "ville": tenant.ville,
            "pays": tenant.pays,
            "email_admin": tenant.email_admin,
            "status": tenant.status,
            "current_plan": tenant.current_plan,
            "max_pharmacies": tenant.max_pharmacies,
            "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None
        }
    }
    

# Ajouter ces modèles pour changer le plan
class ChangePlanSchema(BaseModel):
    new_plan: str
    plan_name: Optional[str] = None
    billing_period: Optional[str] = "mensuel"

class PlanInfoSchema(BaseModel):
    plan: str
    plan_name: str
    max_users: int
    max_products: int
    max_pharmacies: int
    billing_period: Optional[str] = "mensuel"
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None

# Ajouter ces endpoints après la fonction get_tenant_info
@router.post("/change-plan/{tenant_id}")
def change_plan(
    tenant_id: str,
    data: ChangePlanSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Change le plan d'abonnement d'un tenant"""
    # Vérifier que l'utilisateur est admin du tenant
    if current_user.tenant_id != tenant_id or current_user.role != "admin":
        raise HTTPException(403, "Permission refusée")
    
    # Récupérer le tenant
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Valider le nouveau plan
    valid_plans = ["starter", "professional", "enterprise"]
    if data.new_plan not in valid_plans:
        raise HTTPException(400, f"Plan invalide. Options: {', '.join(valid_plans)}")
    
    # Définir les limites selon le nouveau plan
    plan_limits = {
        "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
        "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
        "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
    }
    
    limits = plan_limits[data.new_plan]
    
    # Mettre à jour le plan
    old_plan = tenant.current_plan
    tenant.current_plan = data.new_plan
    tenant.max_users = limits["max_users"]
    tenant.max_products = limits["max_products"]
    tenant.max_pharmacies = limits["max_pharmacies"]
    
    # Mettre à jour le nom du plan dans la config
    if not tenant.config:
        tenant.config = {}
    
    tenant.config["plan_name"] = data.plan_name or data.new_plan.capitalize()
    tenant.config["billing_period"] = data.billing_period
    tenant.config["plan_changed_at"] = datetime.utcnow().isoformat()
    
    # Vérifier si des ajustements sont nécessaires (ex: trop de pharmacies pour le nouveau plan)
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).count()
    
    if active_pharmacies_count > limits["max_pharmacies"]:
        # Désactiver les pharmacies excédentaires
        excess_pharmacies = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == tenant_id,
            Pharmacy.is_active == True
        ).order_by(Pharmacy.created_at.desc()).offset(limits["max_pharmacies"]).all()
        
        for pharmacy in excess_pharmacies:
            pharmacy.is_active = False
    
    db.commit()
    
    logger.info(f"Plan changé pour {tenant.tenant_code}: {old_plan} -> {data.new_plan}")
    
    return {
        "message": "Plan mis à jour avec succès",
        "old_plan": old_plan,
        "new_plan": data.new_plan,
        "plan_name": tenant.config.get("plan_name"),
        "limits": {
            "max_users": limits["max_users"],
            "max_products": limits["max_products"],
            "max_pharmacies": limits["max_pharmacies"]
        },
        "billing_period": data.billing_period
    }

@router.get("/plan-info/{tenant_id}")
def get_plan_info(
    tenant_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère les informations détaillées du plan actuel"""
    if current_user.tenant_id != tenant_id:
        raise HTTPException(403, "Permission refusée")
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Statistiques d'utilisation
    user_count = db.query(User).filter(
        User.tenant_id == tenant_id,
        User.actif == True
    ).count()
    
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant_id,
        Pharmacy.is_active == True
    ).count()
    
    # Compter les produits si nécessaire
    product_count = 0
    if tenant.max_products > 0:  # Si limité
        # Implémenter la logique de comptage des produits selon votre modèle
        pass
    
    return {
        "current_plan": tenant.current_plan,
        "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize(),
        "billing_period": tenant.config.get("billing_period", "mensuel") if tenant.config else "mensuel",
        "limits": {
            "max_users": tenant.max_users,
            "max_products": tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies
        },
        "usage": {
            "users": user_count,
            "pharmacies": active_pharmacies_count,
            "products": product_count
        },
        "remaining": {
            "users": max(0, tenant.max_users - user_count) if tenant.max_users > 0 else "illimité",
            "pharmacies": max(0, tenant.max_pharmacies - active_pharmacies_count) if tenant.max_pharmacies > 0 else "illimité",
            "products": max(0, tenant.max_products - product_count) if tenant.max_products > 0 else "illimité"
        },
        "can_upgrade": tenant.current_plan != "enterprise",
        "can_downgrade": tenant.current_plan != "starter"
    }

# =========================
# MODÈLES POUR LES PAIEMENTS
# =========================
class CreateSubscriptionPaymentSchema(BaseModel):
    plan: str
    billing_period: str = "monthly"  # monthly, yearly
    payment_method: str  # cash, mobile_money, visa, bank_transfer
    amount: float
    reference: Optional[str] = None

class PaymentResponseSchema(BaseModel):
    id: str
    amount: float
    payment_method: str
    status: str
    reference: Optional[str]
    paid_at: datetime
    plan: str
    billing_period: str
    period_start: datetime
    period_end: datetime

# =========================
# ENDPOINTS DE PAIEMENT D'ABONNEMENT
# =========================
@router.post("/subscription/payment")
def create_subscription_payment(
    data: CreateSubscriptionPaymentSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un paiement pour un abonnement
    """
    # Vérifier que l'utilisateur est admin
    if current_user.role != "admin":
        raise HTTPException(403, "Seuls les administrateurs peuvent effectuer des paiements")
    
    # Récupérer le tenant
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Valider le plan
    valid_plans = ["starter", "professional", "enterprise"]
    if data.plan not in valid_plans:
        raise HTTPException(400, f"Plan invalide. Options: {', '.join(valid_plans)}")
    
    # Valider la méthode de paiement
    valid_methods = ["cash", "mobile_money", "visa", "bank_transfer"]
    if data.payment_method not in valid_methods:
        raise HTTPException(400, f"Méthode de paiement invalide. Options: {', '.join(valid_methods)}")
    
    # Calculer la période
    period_start = datetime.utcnow()
    if data.billing_period == "monthly":
        period_end = period_start + timedelta(days=30)
    else:  # yearly
        period_end = period_start + timedelta(days=365)
    
    try:
        # Créer le paiement
        payment = Payment(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            amount=data.amount,
            payment_method=data.payment_method,
            reference=data.reference,
            status="success",
            paid_at=datetime.utcnow()
        )
        db.add(payment)
        
        # Mettre à jour le tenant avec le nouveau plan
        old_plan = tenant.current_plan
        tenant.current_plan = data.plan
        
        # Définir les limites selon le plan
        plan_limits = {
            "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
            "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
            "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
        }
        
        limits = plan_limits.get(data.plan)
        if limits:
            tenant.max_users = limits["max_users"]
            tenant.max_products = limits["max_products"]
            tenant.max_pharmacies = limits["max_pharmacies"]
        
        # Mettre à jour la config
        if not tenant.config:
            tenant.config = {}
        
        tenant.config["subscription"] = {
            "plan": data.plan,
            "plan_name": data.plan.capitalize(),
            "billing_period": data.billing_period,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "last_payment_id": str(payment.id),
            "last_payment_date": payment.paid_at.isoformat()
        }
        
        # Ajouter au statut du tenant
        if tenant.status == "trial":
            tenant.status = "active"
            tenant.activated_at = datetime.utcnow()
        
        db.commit()
        
        logger.info(f"Paiement abonnement réussi pour {tenant.tenant_code}: {old_plan} -> {data.plan}")
        
        return {
            "message": "Paiement réussi. Votre abonnement est maintenant actif.",
            "payment": {
                "id": str(payment.id),
                "amount": payment.amount,
                "payment_method": payment.payment_method,
                "status": payment.status,
                "reference": payment.reference,
                "paid_at": payment.paid_at.isoformat()
            },
            "subscription": {
                "plan": data.plan,
                "billing_period": data.billing_period,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "tenant_status": tenant.status,
                "limits": {
                    "max_users": tenant.max_users,
                    "max_products": tenant.max_products,
                    "max_pharmacies": tenant.max_pharmacies
                }
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors du paiement: {e}", exc_info=True)
        raise HTTPException(500, "Erreur lors du traitement du paiement")

@router.get("/subscription/payments")
def get_subscription_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    skip: int = 0,
    limit: int = 10
):
    """
    Récupère l'historique des paiements d'abonnement
    """
    payments = db.query(Payment).filter(
        Payment.tenant_id == current_user.tenant_id
    ).order_by(Payment.paid_at.desc()).offset(skip).limit(limit).all()
    
    total = db.query(Payment).filter(
        Payment.tenant_id == current_user.tenant_id
    ).count()
    
    return {
        "payments": [
            {
                "id": str(p.id),
                "amount": p.amount,
                "payment_method": p.payment_method,
                "status": p.status,
                "reference": p.reference,
                "paid_at": p.paid_at.isoformat() if p.paid_at else None,
                "sale_id": str(p.sale_id) if p.sale_id else None
            }
            for p in payments
        ],
        "total": total,
        "skip": skip,
        "limit": limit
    }

@router.get("/subscription/status")
def get_subscription_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le statut de l'abonnement actuel
    """
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Vérifier si l'abonnement est actif
    subscription_active = is_subscription_active(db, str(current_user.tenant_id))
    
    # Dernier paiement
    last_payment = db.query(Payment).filter(
        Payment.tenant_id == current_user.tenant_id,
        Payment.status == "success"
    ).order_by(Payment.paid_at.desc()).first()
    
    # Vérifier la date d'expiration
    days_remaining = None
    is_expired = False
    is_near_expiry = False
    
    if tenant.trial_end_date:
        now = datetime.utcnow()
        days_remaining = (tenant.trial_end_date - now).days
        
        if days_remaining < 0:
            is_expired = True
        elif days_remaining <= 3:
            is_near_expiry = True
    
    return {
        "tenant_id": str(tenant.id),
        "tenant_code": tenant.tenant_code,
        "tenant_status": tenant.status,
        "current_plan": tenant.current_plan,
        "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize(),
        "subscription_active": subscription_active,
        "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None,
        "days_remaining": days_remaining,
        "is_expired": is_expired,
        "is_near_expiry": is_near_expiry,
        "limits": {
            "max_users": tenant.max_users,
            "max_products": tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies
        },
        "last_payment": {
            "id": str(last_payment.id) if last_payment else None,
            "amount": last_payment.amount if last_payment else None,
            "payment_method": last_payment.payment_method if last_payment else None,
            "paid_at": last_payment.paid_at.isoformat() if last_payment and last_payment.paid_at else None
        } if last_payment else None
    }

@router.post("/subscription/upgrade")
def upgrade_subscription(
    data: CreateSubscriptionPaymentSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Met à niveau l'abonnement (changement de plan avec paiement)
    """
    # Vérifier que l'utilisateur est admin
    if current_user.role != "admin":
        raise HTTPException(403, "Seuls les administrateurs peuvent mettre à niveau l'abonnement")
    
    # Récupérer le tenant
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Vérifier que c'est bien un upgrade
    current_plan = tenant.current_plan
    plan_hierarchy = ["starter", "professional", "enterprise"]
    
    current_index = plan_hierarchy.index(current_plan) if current_plan in plan_hierarchy else -1
    new_index = plan_hierarchy.index(data.plan) if data.plan in plan_hierarchy else -1
    
    if new_index <= current_index:
        raise HTTPException(400, "Ce n'est pas une mise à niveau valide")
    
    # Utiliser l'endpoint de paiement existant
    return create_subscription_payment(data, db, current_user)

@router.get("/health")
def health_check():
    """Endpoint de santé pour vérifier la connectivité"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "auth-service"
    }

@router.post("/refresh")
def refresh_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Rafraîchit le token d'accès"""
    subscription_active = is_subscription_active(db, str(current_user.tenant_id))
    
    token = create_access_token({
        "sub": str(current_user.id),
        "tenant_id": str(current_user.tenant_id),
        "role": current_user.role,
        "email": current_user.email,
        "subscription_active": subscription_active,
        "pharmacy_id": str(current_user.pharmacy_id) if current_user.pharmacy_id else None
    })
    
    return {
        "access_token": token,
        "token_type": "bearer"
    }

# =========================
# ENDPOINTS TENANT UTILISATEUR
# =========================
@router.get("/tenants/me")
def get_current_tenant_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupère les informations du tenant de l'utilisateur connecté"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Compter les pharmacies actives
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).count()
    
    # Récupérer la pharmacie principale
    main_pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_main == True,
        Pharmacy.is_active == True
    ).first()
    
    response = {
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "slug": tenant.slug,
            "nom_pharmacie": tenant.nom_pharmacie,
            "nom_commercial": tenant.nom_commercial,
            "ville": tenant.ville,
            "pays": tenant.pays,
            "email_admin": tenant.email_admin,
            "nom_proprietaire": tenant.nom_proprietaire,
            "telephone_principal": tenant.telephone_principal,
            "telephone_secondaire": tenant.telephone_secondaire,
            "type_pharmacie": tenant.type_pharmacie,
            "status": tenant.status,
            "current_plan": tenant.current_plan,
            "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize(),
            "max_users": tenant.max_users,
            "max_products": tenant.max_products,
            "max_pharmacies": tenant.max_pharmacies,
            "active_pharmacies": active_pharmacies_count,
            "trial_start_date": tenant.trial_start_date.isoformat() if tenant.trial_start_date else None,
            "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None,
            "activated_at": tenant.activated_at.isoformat() if tenant.activated_at else None,
            "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
            "config": tenant.config or {}
        }
    }
    
    if main_pharmacy:
        response["main_pharmacy"] = {
            "id": str(main_pharmacy.id),
            "name": main_pharmacy.name,
            "address": main_pharmacy.address,
            "city": main_pharmacy.city,
            "phone": main_pharmacy.phone,
            "email": main_pharmacy.email,
            "pharmacy_code": main_pharmacy.pharmacy_code,
            "is_main": main_pharmacy.is_main,
            "is_active": main_pharmacy.is_active
        }
    
    return response

@router.get("/tenant-users")
def get_tenant_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupère tous les utilisateurs du tenant courant"""
    
    users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True
    ).order_by(User.nom_complet).all()
    
    users_list = []
    for user in users:
        # Récupérer les pharmacies associées
        user_pharmacies = db.query(Pharmacy).join(
            UserPharmacy, UserPharmacy.pharmacy_id == Pharmacy.id
        ).filter(
            UserPharmacy.user_id == user.id,
            Pharmacy.is_active == True
        ).all()
        
        user_data = {
            "id": str(user.id),
            "nom_complet": user.nom_complet,
            "email": user.email,
            "role": user.role,
            "telephone": user.telephone,
            "actif": user.actif,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "pharmacies": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "pharmacy_code": p.pharmacy_code
                }
                for p in user_pharmacies
            ]
        }
        users_list.append(user_data)
    
    return users_list

@router.get("/session/test")
def test_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test de session pour vérifier que l'authentification fonctionne"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    return {
        "authenticated": True,
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet,
            "role": current_user.role
        },
        "tenant": {
            "id": str(tenant.id) if tenant else None,
            "nom_pharmacie": tenant.nom_pharmacie if tenant else None,
            "tenant_code": tenant.tenant_code if tenant else None
        },
        "timestamp": datetime.utcnow().isoformat()
    }

# =========================
# ENDPOINTS DE SANTÉ ET MÉTADONNÉES
# =========================

@router.get("/health")
def health_check():
    """Endpoint de santé pour vérifier la connectivité"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "auth-service",
        "version": "1.0.0",
        "endpoints_available": [
            "/api/v1/auth/health",
            "/api/v1/auth/login",
            "/api/v1/auth/tenants/register",
            "/api/v1/auth/verify-sms",
            "/api/v1/auth/subscription/status"
        ]
    }

@router.get("/api-status")
def api_status():
    """Retourne le statut de toutes les APIs"""
    return {
        "status": "operational",
        "timestamp": datetime.utcnow().isoformat(),
        "apis": {
            "auth": {
                "status": "active",
                "version": "1.0.0",
                "endpoints": 25
            },
            "health": {
                "status": "active",
                "endpoint": "/api/v1/auth/health"
            }
        }
    }

# Ajouter cet endpoint pour la compatibilité avec auth_service.py
@router.get("/tenants/me")
def get_current_tenant_info_v1(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupère les informations du tenant de l'utilisateur connecté (version v1)"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    # Compter les pharmacies actives
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).count()
    
    # Récupérer la pharmacie principale
    main_pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_main == True,
        Pharmacy.is_active == True
    ).first()
    
    # Récupérer toutes les pharmacies actives
    pharmacies = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()
    
    response = {
        "success": True,
        "data": {
            "tenant": {
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "slug": tenant.slug,
                "nom_pharmacie": tenant.nom_pharmacie,
                "nom_commercial": tenant.nom_commercial,
                "ville": tenant.ville,
                "pays": tenant.pays,
                "email_admin": tenant.email_admin,
                "nom_proprietaire": tenant.nom_proprietaire,
                "telephone_principal": tenant.telephone_principal,
                "telephone_secondaire": tenant.telephone_secondaire,
                "type_pharmacie": tenant.type_pharmacie,
                "status": tenant.status,
                "current_plan": tenant.current_plan,
                "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan.capitalize(),
                "max_users": tenant.max_users,
                "max_products": tenant.max_products,
                "max_pharmacies": tenant.max_pharmacies,
                "active_pharmacies": active_pharmacies_count,
                "trial_start_date": tenant.trial_start_date.isoformat() if tenant.trial_start_date else None,
                "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None,
                "activated_at": tenant.activated_at.isoformat() if tenant.activated_at else None,
                "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
                "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
                "config": tenant.config or {}
            },
            "pharmacies": [
                {
                    "id": str(p.id),
                    "name": p.name,
                    "address": p.address,
                    "city": p.city,
                    "phone": p.phone,
                    "email": p.email,
                    "pharmacy_code": p.pharmacy_code,
                    "is_main": p.is_main,
                    "is_active": p.is_active,
                    "created_at": p.created_at.isoformat() if p.created_at else None
                }
                for p in pharmacies
            ]
        }
    }
    
    if main_pharmacy:
        response["data"]["main_pharmacy"] = {
            "id": str(main_pharmacy.id),
            "name": main_pharmacy.name,
            "address": main_pharmacy.address,
            "city": main_pharmacy.city,
            "phone": main_pharmacy.phone,
            "email": main_pharmacy.email,
            "pharmacy_code": main_pharmacy.pharmacy_code,
            "is_main": main_pharmacy.is_main,
            "is_active": main_pharmacy.is_active
        }
    
    return response

@router.post("/super-admin/setup", status_code=status.HTTP_201_CREATED)
async def setup_super_admin(data: SuperAdminSetup, db: Session = Depends(get_db)):
    master_key = os.getenv("INITIAL_SETUP_KEY")
    if not master_key or data.setup_key != master_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clé d'installation invalide ou non configurée.")

    # Ici on considère super admin = role == "super_admin"
    existing_admin = db.query(User).filter(User.role == "super_admin").first()
    if existing_admin:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le système est déjà initialisé.")

    new_admin = User(
        tenant_id=uuid.uuid4(),  # ⚠️ ton modèle exige tenant_id NOT NULL -> il faut un tenant “system”
        email=data.email.lower(),
        password_hash=get_password_hash(data.password),
        nom_complet=data.nom_complet,
        actif=True,
        role="super_admin",
    )

    db.add(new_admin)
    db.commit()
    db.refresh(new_admin)

    return {"message": "Super Admin créé avec succès."}