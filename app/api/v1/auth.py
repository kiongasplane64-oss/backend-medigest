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
import string
import secrets

from app.api.deps import get_current_user
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
    get_password_hash,
    create_token_pair as security_create_token_pair
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
    
    @field_validator("pays")
    def validate_country(cls, v: str) -> str:
        """Convertit les noms de pays en codes ISO si nécessaire"""
        if not v:
            return "CD"
        
        country_mapping = {
            "rdc": "CD",
            "congo": "CD",
            "république démocratique du congo": "CD",
            "republique democratique du congo": "CD",
            "congo kinshasa": "CD",
            "congo-kinshasa": "CD",
            "rd congo": "CD",
            "côte d'ivoire": "CI",
            "cote d'ivoire": "CI",
            "cameroon": "CM",
            "cameroun": "CM",
            "senegal": "SN",
            "sénégal": "SN",
            "france": "FR",
            "belgique": "BE",
            "belgium": "BE",
        }
        
        if len(v) == 2 and v.isalpha():
            return v.upper()
        
        normalized = v.lower().strip()
        if normalized in country_mapping:
            return country_mapping[normalized]
        
        if "congo" in normalized or "kinshasa" in normalized:
            return "CD"
        
        raise ValueError(
            f"Pays '{v}' non reconnu. Utilisez le code ISO à 2 lettres "
            f"(ex: CD pour RDC, CI pour Côte d'Ivoire, FR pour France)"
        )


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class ResetRequestSchema(BaseModel):
    email: EmailStr


class ResetConfirmSchema(BaseModel):
    email: EmailStr
    code: str
    new_password: str


class ExistingPhoneVerificationRequest(BaseModel):
    phone: str
    email: Optional[str] = None


class ExistingPhoneVerificationConfirm(BaseModel):
    phone: str
    code: str
    action: str = "continue"


class SuperAdminSetup(BaseModel):
    email: EmailStr
    password: str
    nom_complet: str
    setup_key: str


class TokenRefreshSchema(BaseModel):
    refresh_token: str


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class ChangePlanSchema(BaseModel):
    new_plan: str
    plan_name: Optional[str] = None
    billing_period: Optional[str] = "mensuel"


class CreateSubscriptionPaymentSchema(BaseModel):
    plan: str
    billing_period: str = "monthly"
    payment_method: str
    amount: float
    reference: Optional[str] = None


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
    
    while db.query(Tenant).filter(Tenant.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1
        if counter > 100:
            slug = f"{base_slug}-{str(uuid.uuid4())[:8]}"
            break
    
    return slug


def generate_unique_tenant_code(nom_pharmacie: str, db: Session) -> str:
    """Génère un code unique pour un tenant avec vérification"""
    prefix = nom_pharmacie[:3].upper().replace(' ', '')
    if len(prefix) < 3:
        prefix = prefix + 'PH'
    
    while True:
        random_suffix = str(random.randint(100, 999))
        tenant_code = f"{prefix}{random_suffix}"
        
        if not db.query(Tenant).filter(Tenant.tenant_code == tenant_code).first():
            return tenant_code
        
        counter += 1
        if counter > 10:
            return f"PH{str(uuid.uuid4())[:8].upper()}"


def is_subscription_active(db: Session, tenant_id: str) -> bool:
    """Vérifie si l'abonnement est actif pour un tenant donné"""
    try:
        return check_subscription_status(db, tenant_id)
    except Exception as e:
        logger.error(f"Erreur lors de la vérification de l'abonnement: {e}")
        return False


ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crée un refresh token JWT."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({
        "exp": expire,
        "type": "refresh"
    })
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_token_pair(user: User, subscription_active: bool, pharmacy_id: Optional[str] = None) -> dict:
    """Génère un couple access_token + refresh_token cohérent."""
    access_payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "role": user.role,
        "email": user.email,
        "subscription_active": subscription_active,
        "pharmacy_id": pharmacy_id,
        "type": "access"
    }

    refresh_payload = {
        "sub": str(user.id),
        "tenant_id": str(user.tenant_id) if user.tenant_id else None,
        "role": user.role,
        "email": user.email,
        "type": "refresh"
    }

    access_token = create_access_token(
        access_payload,
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    refresh_token = create_refresh_token(
        refresh_payload,
        expires_delta=timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "refresh_expires_in": REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    }


def decode_token_safely(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except Exception as e:
        logger.error(f"Erreur décodage token: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré"
        )


# =========================
# ENDPOINTS D'INSCRIPTION
# =========================
@router.post("/tenants/register", status_code=201)
def register_tenant(data: TenantRegisterSchema, db: Session = Depends(get_db)):
    """Inscription d'un nouveau tenant (pharmacie) avec création automatique de l'abonnement d'essai"""
    
    # =========================
    # 1. VÉRIFICATIONS PRÉLIMINAIRES
    # =========================
    
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
        raise HTTPException(
            status_code=400,
            detail="Mot de passe trop long (maximum 72 caractères)"
        )

    # =========================
    # 2. GESTION DU PLAN D'ABONNEMENT
    # =========================
    
    if not data.plan:
        raise HTTPException(400, "Plan d'abonnement requis")

    plan = data.plan
    plan_name = data.plan_name if data.plan_name else plan.capitalize()

    plan_limits = {
        "starter": {"max_users": 2, "max_products": 500, "max_pharmacies": 1},
        "professional": {"max_users": 10, "max_products": 0, "max_pharmacies": 3},
        "enterprise": {"max_users": 0, "max_products": 0, "max_pharmacies": 0}
    }

    if plan not in plan_limits:
        raise HTTPException(
            status_code=400,
            detail=f"Plan invalide. Options: {', '.join(plan_limits.keys())}"
        )

    limits = plan_limits.get(plan)

    # =========================
    # 3. GÉNÉRATION DES IDENTIFIANTS UNIQUES
    # =========================
    
    tenant_code = generate_unique_tenant_code(data.nom_pharmacie, db)
    slug = generate_unique_slug(data.nom_pharmacie, db)
    pharmacy_code = f"{tenant_code}001"

    # =========================
    # 4. CRÉATION DU TENANT
    # =========================
    
    try:
        tenant = Tenant(
            tenant_code=tenant_code,
            slug=slug,
            nom_pharmacie=data.nom_pharmacie,
            nom_commercial=data.nom_pharmacie,
            ville=data.ville,
            pays=data.pays,
            telephone_principal=data.telephone,
            email_admin=data.email.lower(),
            nom_proprietaire=data.nom_complet,
            type_pharmacie=data.type_pharmacie,
            status="active",
            max_users=limits["max_users"],
            max_products=limits["max_products"],
            current_plan=plan,
            max_pharmacies=limits["max_pharmacies"],
            trial_start_date=datetime.utcnow(),
            trial_end_date=datetime.utcnow() + timedelta(days=14),
            config={"plan_name": plan_name}
        )
        db.add(tenant)
        db.flush()

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

    # =========================
    # 5. CRÉATION DE L'UTILISATEUR ADMIN
    # =========================
    
    try:
        hashed_password = hash_password(data.password)
        admin = User(
            tenant_id=tenant.id,
            nom_complet=data.nom_complet,
            email=data.email.lower(),
            password_hash=hashed_password,
            role="admin",
            actif=True,
            telephone=data.telephone,
            login_attempts=0,
        )
        db.add(admin)
        db.flush()
        
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
    
    # =========================
    # 6. CRÉATION DE LA PHARMACIE PRINCIPALE
    # =========================

    try:
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
        db.flush()
        
        # CRÉATION DE LA BRANCHE PAR DÉFAUT
        from app.models.pharmacy import Branch
        
        branch_code = f"BR-{tenant_code}-001"
        
        default_branch = Branch(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            parent_pharmacy_id=pharmacy.id,
            name="Pharmacie Principale",
            code=branch_code,
            address=data.ville,
            city=data.ville,
            country=data.pays,
            phone=data.telephone,
            email=data.email.lower(),
            is_active=True,
            is_main_branch=True,
            manager_id=admin.id,
            manager_name=data.nom_complet,
            created_by=admin.id,
            config={
                "workingHours": {
                    "enabled": True,
                    "startTime": "08:00",
                    "endTime": "20:00",
                    "timezone": "Africa/Kinshasa",
                    "daysOff": {
                        "monday": True,
                        "tuesday": True,
                        "wednesday": True,
                        "thursday": True,
                        "friday": True,
                        "saturday": True,
                        "sunday": False
                    }
                },
                "marginConfig": {
                    "defaultMargin": 30,
                    "minMargin": 10,
                    "maxMargin": 50
                },
                "automaticPricing": {
                    "enabled": True,
                    "method": "percentage",
                    "value": 25
                },
                "inheritedFromParent": True
            }
        )
        db.add(default_branch)
        db.flush()
        
        logger.info(f"Branche principale créée: {default_branch.name}")

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création pharmacie/branche: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "pharmacy_creation_failed",
                "message": "Erreur lors de la création de la pharmacie",
                "suggestion": "Contactez le support technique"
            }
        )

    # =========================
    # 7. ASSOCIATION ADMIN-PHARMACIE
    # =========================

    try:
        association = UserPharmacy(
            user_id=admin.id,
            pharmacy_id=pharmacy.id,
            is_primary=True,
            role_in_pharmacy="admin",
            can_manage=True
        )
        db.add(association)
        
        admin.active_pharmacy_id = pharmacy.id
        admin.active_branch_id = default_branch.id
        db.add(admin)
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur association admin-pharmacie: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "association_failed",
                "message": "Erreur lors de l'association admin-pharmacie",
                "suggestion": "Contactez le support technique"
            }
        )
    
    # =========================
    # 8. CRÉATION DE L'ABONNEMENT D'ESSAI
    # =========================

    from app.services.subscription_service import create_trial_subscription
    import inspect

    try:
        sig = inspect.signature(create_trial_subscription)
        params = sig.parameters
        
        kwargs = {
            'db': db,
            'user_id': admin.id,
            'tenant_id': tenant.id
        }
        
        if 'trial_days' in params:
            kwargs['trial_days'] = 14
        elif 'days' in params:
            kwargs['days'] = 14
        elif 'duration' in params:
            kwargs['duration'] = 14
        elif 'duration_days' in params:
            kwargs['duration_days'] = 14
        
        trial_subscription = create_trial_subscription(**kwargs)
        logger.info(f"Abonnement d'essai créé pour l'utilisateur {admin.id}")
        
    except TypeError as e:
        db.rollback()
        logger.error(f"Erreur de signature dans create_trial_subscription: {e}")
        
        try:
            trial_subscription = create_trial_subscription(db, admin.id, tenant.id)
        except Exception as e2:
            db.rollback()
            logger.error(f"Erreur création abonnement d'essai (second essai): {e2}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "subscription_creation_failed",
                    "message": "Erreur lors de la création de l'abonnement d'essai",
                    "suggestion": "Contactez le support technique"
                }
            )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création abonnement d'essai: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "subscription_creation_failed",
                "message": "Erreur lors de la création de l'abonnement d'essai",
                "suggestion": "Contactez le support technique"
            }
        )
    
    # =========================
    # 9. VALIDATION FINALE
    # =========================
    
    try:
        db.commit()
        logger.info(f"Tenant créé avec succès: {tenant_code} - Admin: {admin.email}")
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur commit final: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "database_error",
                "message": "Erreur lors de l'enregistrement final",
                "suggestion": "Veuillez réessayer dans quelques instants"
            }
        )

    # =========================
    # 10. RÉPONSE AU CLIENT
    # =========================
    
    response = {
        "status": "success",
        "message": "Pharmacie créée avec succès. Vous pouvez maintenant vous connecter.",
        "data": {
            "tenant_id": str(tenant.id),
            "user_id": str(admin.id),
            "tenant_code": tenant_code,
            "pharmacy_id": str(pharmacy.id),
            "plan": "trial",
            "plan_name": "Essai gratuit",
            "trial_end_date": trial_subscription.end_date.isoformat(),
            "trial_days": 14,
            "subscription": {
                "id": str(trial_subscription.id),
                "status": trial_subscription.status,
                "days_remaining": trial_subscription.days_remaining(),
                "is_trial": trial_subscription.is_trial(),
                "mode": trial_subscription.get_mode()
            },
            "limits": {
                "max_users": limits["max_users"],
                "max_products": limits["max_products"],
                "max_pharmacies": limits["max_pharmacies"]
            },
            "created_at": datetime.utcnow().isoformat(),
            "trial_end_date": trial_subscription.end_date.isoformat()
        },
        "next_steps": {
            "login": {
                "message": "Connectez-vous pour accéder à votre compte",
                "action": "POST /api/v1/auth/login",
                "required_data": {
                    "email": data.email,
                    "password": "votre_mot_de_passe"
                }
            },
            "welcome_sms": {
                "message": "Un SMS de bienvenue sera envoyé lors de votre première connexion",
                "note": "Le SMS sera envoyé automatiquement après votre première connexion réussie"
            },
            "trial_info": f"Vous bénéficiez de 14 jours d'essai gratuit jusqu'au {trial_subscription.end_date.strftime('%d/%m/%Y')}",
            "dashboard_access": "Connectez-vous pour accéder à votre tableau de bord"
        },
        "recommendations": [
            "Sauvegardez vos identifiants dans un endroit sécurisé",
            "Connectez-vous pour accéder à votre tableau de bord",
            "Complétez le profil de votre pharmacie après connexion",
            "Explorez les fonctionnalités pendant votre période d'essai"
        ]
    }
    
    return response


# =========================
# ENDPOINTS DE CONNEXION (SANS OTP)
# =========================
@router.post("/login")
def login(data: LoginSchema, db: Session = Depends(get_db)):
    """Connexion utilisateur avec génération access_token + refresh_token."""
    email = data.email.lower().strip()
    logger.info(f"Tentative de login pour: {email}")

    user = db.query(User).filter(User.email == email).first()

    if not user:
        logger.warning(f"Utilisateur non trouvé: {email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides"
        )

    # Vérification verrouillage
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Compte temporairement bloqué. Réessayez dans {remaining} minutes."
        )

    # Vérification mot de passe
    if not verify_password(data.password, user.password_hash):
        user.login_attempts = (user.login_attempts or 0) + 1

        if user.login_attempts >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MIN)
            user.login_attempts = 0
            logger.warning(f"Compte verrouillé après trop d'échecs: {email}")

        db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides"
        )

    # Vérifier si c'est la première connexion (pour envoyer le SMS de bienvenue)
    is_first_login = user.last_login is None
    
    # Si c'est la première connexion, envoyer un SMS de bienvenue
    if is_first_login and user.telephone:
        try:
            formatted_phone = format_phone_for_twilio(user.telephone)
            welcome_message = (
                f"Bienvenue sur MEDIGEST ! 🎉\n\n"
                f"Votre compte a été créé avec succès.\n"
                f"Vous pouvez maintenant accéder à votre tableau de bord.\n\n"
                f"Email: {user.email}\n"
                f"Si vous avez des questions, contactez notre support."
            )
            send_sms_with_fallback(formatted_phone, welcome_message)
            logger.info(f"SMS de bienvenue envoyé à {email}")
        except Exception as e:
            logger.error(f"Erreur envoi SMS de bienvenue à {email}: {e}")

    # Récupération du tenant et des pharmacies
    tenant = None
    tenant_data = None
    pharmacies = []
    main_pharmacy = None
    subscription_active = False

    if user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

        if tenant:
            pharmacies = db.query(Pharmacy).filter(
                Pharmacy.tenant_id == tenant.id,
                Pharmacy.is_active == True
            ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()

            main_pharmacy = next(
                (pharmacy for pharmacy in pharmacies if pharmacy.is_main),
                pharmacies[0] if pharmacies else None
            )

            subscription_active = is_subscription_active(db, str(user.tenant_id))

            tenant_data = {
                "id": str(tenant.id),
                "tenant_code": tenant.tenant_code,
                "nom_pharmacie": tenant.nom_pharmacie,
                "nom_commercial": tenant.nom_commercial,
                "ville": tenant.ville,
                "pays": tenant.pays,
                "email_admin": tenant.email_admin,
                "status": tenant.status,
                "current_plan": tenant.current_plan,
                "plan_name": tenant.config.get("plan_name") if tenant.config else tenant.current_plan,
                "max_users": tenant.max_users,
                "max_products": tenant.max_products,
                "max_pharmacies": tenant.max_pharmacies,
                "trial_end_date": tenant.trial_end_date.isoformat() if tenant.trial_end_date else None
            }
        else:
            logger.warning(f"Tenant introuvable pour l'utilisateur {email}: {user.tenant_id}")
            subscription_active = False
    else:
        logger.info(f"Utilisateur sans tenant (rôle: {user.role})")
        subscription_active = True

    # Réinitialiser les tentatives de login
    user.login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()

    token_pair = create_token_pair(
        user=user,
        subscription_active=subscription_active,
        pharmacy_id=str(main_pharmacy.id) if main_pharmacy else None
    )

    response_data = {
        **token_pair,
        "subscription_active": subscription_active,
        "is_first_login": is_first_login,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "nom_complet": user.nom_complet,
            "role": user.role,
            "tenant_id": str(user.tenant_id) if user.tenant_id else None,
            "actif": user.actif,
            "telephone": user.telephone
        },
        "tenant": tenant_data,
        "pharmacies": [
            {
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
            }
            for pharmacy in pharmacies
        ]
    }

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

    logger.info(f"Login réussi pour: {email}")
    return response_data


# =========================
# ENDPOINTS RÉINITIALISATION MOT DE PASSE
# =========================
@router.post("/password/reset/request")
def request_reset(data: ResetRequestSchema, db: Session = Depends(get_db)):
    """Demande de réinitialisation de mot de passe - envoie un code par SMS"""
    user = db.query(User).filter(User.email == data.email.lower()).first()
    if not user:
        return {"message": "Si le compte existe, un code sera envoyé"}

    code = generate_otp()
    user.reset_code = code
    user.reset_expires = datetime.utcnow() + timedelta(minutes=RESET_EXPIRATION_MIN)
    db.commit()

    sms_sent = False
    try:
        if user.telephone:
            formatted_phone = format_phone_for_twilio(user.telephone)
            send_sms(formatted_phone, f"Code réinitialisation: {code}")
            send_whatsapp(formatted_phone, f"Code réinitialisation: {code}")
            sms_sent = True
    except Exception as e:
        logger.error(f"Erreur envoi SMS/WhatsApp: {e}")

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
# ENDPOINTS DE VÉRIFICATION DE DISPONIBILITÉ
# =========================
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
        phone_clean = re.sub(r'\D', '', phone)
        existing_phone = db.query(Tenant).filter(Tenant.telephone_principal == phone_clean).first()
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


@router.post("/check-phone-exists")
async def check_phone_exists(
    phone: str,
    db: Session = Depends(get_db)
):
    """Vérifie si un numéro existe déjà"""
    phone_clean = re.sub(r'\D', '', phone)
    
    tenant = db.query(Tenant).filter(
        Tenant.telephone_principal == phone_clean
    ).first()
    
    if not tenant:
        user = db.query(User).filter(User.telephone == phone_clean).first()
        if user:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
    
    if not tenant:
        return {"exists": False, "message": "Numéro disponible"}
    
    email = tenant.email_admin if tenant else None
    if email:
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
        "suggestions": [
            "Si c'est votre compte, connectez-vous avec votre email",
            "Si ce n'est pas votre compte, utilisez un autre numéro"
        ]
    }


# =========================
# ENDPOINTS UTILISATEUR
# =========================
@router.get("/me")
def get_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    tenant = None
    pharmacies = []
    current_pharmacy = None
    subscription_active = True

    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()

        pharmacies = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == current_user.tenant_id,
            Pharmacy.is_active == True
        ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()

        current_pharmacy = next((p for p in pharmacies if p.is_main), pharmacies[0] if pharmacies else None)
        subscription_active = is_subscription_active(db, str(current_user.tenant_id))

    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "nom_complet": current_user.nom_complet,
            "role": current_user.role,
            "tenant_id": str(current_user.tenant_id) if current_user.tenant_id else None,
            "actif": current_user.actif,
            "telephone": current_user.telephone
        },
        "tenant": {
            "id": str(tenant.id),
            "tenant_code": tenant.tenant_code,
            "nom_pharmacie": tenant.nom_pharmacie,
            "status": tenant.status
        } if tenant else None,
        "subscription_active": subscription_active,
        "current_pharmacy": {
            "id": str(current_pharmacy.id),
            "name": current_pharmacy.name,
            "pharmacy_code": current_pharmacy.pharmacy_code
        } if current_pharmacy else None
    }


@router.get("/tenants/me")
def get_current_tenant_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupère les informations du tenant de l'utilisateur connecté"""
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    active_pharmacies_count = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == tenant.id,
        Pharmacy.is_active == True
    ).count()
    
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


# =========================
# ENDPOINTS ABONNEMENT
# =========================
@router.get("/subscription/status")
def get_subscription_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère le statut de l'abonnement actuel"""
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    subscription_active = is_subscription_active(db, str(current_user.tenant_id))
    
    last_payment = db.query(Payment).filter(
        Payment.tenant_id == current_user.tenant_id,
        Payment.status == "success"
    ).order_by(Payment.paid_at.desc()).first()
    
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


@router.post("/subscription/payment")
def create_subscription_payment(
    data: CreateSubscriptionPaymentSchema,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Crée un paiement pour un abonnement"""
    if current_user.role != "admin":
        raise HTTPException(403, "Seuls les administrateurs peuvent effectuer des paiements")
    
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(404, "Tenant non trouvé")
    
    valid_plans = ["starter", "professional", "enterprise"]
    if data.plan not in valid_plans:
        raise HTTPException(400, f"Plan invalide. Options: {', '.join(valid_plans)}")
    
    valid_methods = ["cash", "mobile_money", "visa", "bank_transfer"]
    if data.payment_method not in valid_methods:
        raise HTTPException(400, f"Méthode de paiement invalide. Options: {', '.join(valid_methods)}")
    
    period_start = datetime.utcnow()
    if data.billing_period == "monthly":
        period_end = period_start + timedelta(days=30)
    else:
        period_end = period_start + timedelta(days=365)
    
    try:
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
        
        old_plan = tenant.current_plan
        tenant.current_plan = data.plan
        
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


# =========================
# ENDPOINTS SUPER ADMIN
# =========================
@router.post("/super-admin/verify-key")
def verify_super_admin_key(data: dict, db: Session = Depends(get_db)):
    """Vérifie la clé d'accès super administrateur"""
    key = data.get("key", "")
    
    logger.info(f"Vérification clé super admin - Longueur: {len(key)}")
    
    master_key = os.getenv("SUPER_ADMIN_ACCESS_KEY")
    create_key = os.getenv("SUPER_ADMIN_CREATE_KEY")
    
    if master_key and key == master_key:
        logger.info("✅ Clé maître valide")
        return {"valid": True, "access_type": "full"}
    
    if create_key and key == create_key:
        logger.info("✅ Clé de création valide")
        return {"valid": True, "access_type": "setup"}
    
    logger.warning(f"❌ Clé invalide: {key[:5]}...")
    return {"valid": False, "message": "Clé d'accès invalide"}


@router.post("/super-admin/setup", status_code=status.HTTP_201_CREATED)
async def setup_super_admin(data: SuperAdminSetup, db: Session = Depends(get_db)):
    """Crée le premier super administrateur (nécessite une clé d'installation)"""
    master_key = os.getenv("INITIAL_SETUP_KEY")
    logger.info(f"Tentative création super admin - Setup key fournie: {data.setup_key[:5]}...")
    
    if not master_key:
        logger.error("INITIAL_SETUP_KEY non configurée")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Clé d'installation non configurée sur le serveur."
        )
    
    if data.setup_key != master_key:
        logger.warning(f"Clé d'installation invalide: {data.setup_key[:5]}... vs {master_key[:5]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Clé d'installation invalide."
        )

    existing_admin = db.query(User).filter(User.role == "super_admin").first()
    if existing_admin:
        logger.warning("Tentative de création d'un super admin alors qu'il existe déjà")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Un super administrateur existe déjà. Utilisez l'interface de connexion normale."
        )

    new_admin = User(
        tenant_id=None,  
        email=data.email.lower(),
        password_hash=get_password_hash(data.password),
        nom_complet=data.nom_complet,
        actif=True,
        role="super_admin",
        created_at=datetime.utcnow(),
    )

    try:
        db.add(new_admin)
        db.commit()
        db.refresh(new_admin)
        
        logger.info(f"✅ Super Admin créé avec succès: {new_admin.email}")
        
        return {
            "message": "Super Admin créé avec succès.",
            "credentials": {
                "email": new_admin.email,
                "password": data.password
            } if os.getenv("ENV") != "production" else None
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création super admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création: {str(e)}"
        )


# =========================
# ENDPOINTS DE RAFRAÎCHISSEMENT DE TOKEN
# =========================
@router.post("/refresh", response_model=TokenPairResponse)
def refresh_token(data: TokenRefreshSchema, db: Session = Depends(get_db)):
    """Rafraîchit le token d'accès à partir d'un refresh token valide."""
    payload = decode_token_safely(data.refresh_token)

    token_type = payload.get("type")
    if token_type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Type de token invalide"
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide"
        )

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )

    if not user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte inactif"
        )

    main_pharmacy = None
    subscription_active = True

    if user.tenant_id:
        main_pharmacy = db.query(Pharmacy).filter(
            Pharmacy.tenant_id == user.tenant_id,
            Pharmacy.is_main == True,
            Pharmacy.is_active == True
        ).first()
        subscription_active = is_subscription_active(db, str(user.tenant_id))

    token_pair = create_token_pair(
        user=user,
        subscription_active=subscription_active,
        pharmacy_id=str(main_pharmacy.id) if main_pharmacy else None
    )

    logger.info(f"Refresh token réussi pour {user.email}")
    return token_pair


# =========================
# ENDPOINTS DE SANTÉ
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
                "endpoints": 20
            },
            "health": {
                "status": "active",
                "endpoint": "/api/v1/auth/health"
            }
        }
    }


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


@router.get("/current-session", status_code=status.HTTP_200_OK)
def get_current_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Récupère la session courante de l'utilisateur."""
    
    session_id = None
    session_number = None
    pos_id = None
    pos_name = None
    pharmacy_id = None
    
    pharmacy = db.query(Pharmacy).filter(
        Pharmacy.tenant_id == current_user.tenant_id,
        Pharmacy.is_active == True,
        Pharmacy.is_main == True
    ).first()
    
    if pharmacy:
        pharmacy_id = pharmacy.id
        pos_id = str(pharmacy.id)
        pos_name = pharmacy.name
    else:
        pos_id = f"POS-{str(current_user.id)[:6]}"
        pos_name = "Caisse principale"
    
    alphabet = string.digits
    session_number = ''.join(secrets.choice(alphabet) for _ in range(4))
    session_id = f"{str(current_user.id)[:6]}_{datetime.utcnow().strftime('%Y%m%d')}_{session_number}"
    
    return {
        "sessionId": session_id,
        "sessionNumber": session_number,
        "posId": pos_id,
        "posName": pos_name,
        "pharmacyId": str(pharmacy_id) if pharmacy_id else None,
        "userId": str(current_user.id),
        "userName": current_user.nom_complet,
        "userEmail": current_user.email,
        "userRole": current_user.role,
        "startedAt": datetime.utcnow().isoformat(),
        "status": "active"
    }