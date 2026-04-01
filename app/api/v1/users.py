# app/api/v1/users.py - Version complète corrigée avec gestion des associations user_pharmacy
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query, Body
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
import logging
import traceback
import sys
from datetime import datetime, timedelta
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, field_validator
from app.db.session import get_db
from app.models.user import User
from app.models.user_pharmacy import UserPharmacy
from app.models.pharmacy import Pharmacy
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListSchema
from app.core.security import hash_password, verify_password
from app.api.v1.auth import get_current_user
from app.services.audit_service import log_action
from app.models.tenant import Tenant
from sqlalchemy import func

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["Users"])

# =========================
# SCHEMAS ADDITIONNELS
# =========================

class SimpleUserProfile(BaseModel):
    """Schéma simplifié pour affichage utilisateur"""
    id: UUID
    email: EmailStr
    nom_complet: str
    role: str
    telephone: Optional[str] = None
    actif: bool
    date_creation: Optional[datetime] = None
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class UserCreateRequest(BaseModel):
    """
    Schéma pour la création d'utilisateur - Compatible avec le frontend
    Le frontend envoie 'full_name' mais nous stockons 'nom_complet'
    """
    full_name: str = Field(..., min_length=2, max_length=100, alias="full_name")
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: str = Field(..., pattern="^(admin|pharmacien|vendeur|caissier|gestionnaire|comptable|preparateur|stockiste)$")
    telephone: Optional[str] = Field(None, max_length=20)
    adresse: Optional[str] = None
    pharmacy_id: Optional[str] = None  # ID de la pharmacie à associer
    branch_id: Optional[str] = None     # ID de la branche à associer
    is_active: Optional[bool] = True
    permissions: Optional[Dict[str, bool]] = None
    
    class Config:
        populate_by_name = True
        from_attributes = True
    
    @field_validator('full_name')
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        """Valide et nettoie le nom complet"""
        if not v or len(v.strip()) < 2:
            raise ValueError('Le nom complet doit contenir au moins 2 caractères')
        return v.strip()
    
    @field_validator('telephone')
    @classmethod
    def validate_telephone(cls, v: Optional[str]) -> Optional[str]:
        """Valide le format du téléphone"""
        if v:
            cleaned = v.replace(' ', '').replace('-', '')
            if not cleaned.replace('+', '').isdigit():
                raise ValueError('Le numéro de téléphone doit contenir uniquement des chiffres et éventuellement +')
        return v


class UserUpdateRequest(BaseModel):
    """Schéma pour la mise à jour d'utilisateur"""
    nom_complet: Optional[str] = Field(None, min_length=2, max_length=100)
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=8)
    role: Optional[str] = Field(None, pattern="^(admin|pharmacien|vendeur|caissier|gestionnaire|comptable|preparateur|stockiste)$")
    telephone: Optional[str] = None
    adresse: Optional[str] = None
    actif: Optional[bool] = None
    active_pharmacy_id: Optional[str] = None  # Pour changer la pharmacie active
    active_branch_id: Optional[str] = None     # Pour changer la branche active
    
    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    """Schéma pour le changement de mot de passe"""
    old_password: str
    new_password: str = Field(..., min_length=8)


# =========================
# FONCTIONS UTILITAIRES
# =========================

def is_valid_uuid(uuid_string):
    """Vérifie si une chaîne est un UUID valide"""
    try:
        UUID(uuid_string)
        return True
    except ValueError:
        return False


def get_default_permissions(role: str) -> dict:
    """
    Retourne les permissions par défaut selon le rôle.
    """
    permissions = {
        "admin": {
            "gestion_utilisateurs": True,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": True,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "pharmacien": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "vendeur": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": False
        },
        "caissier": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": True,
            "gestion_fournisseurs": False
        },
        "gestionnaire": {
            "gestion_utilisateurs": True,
            "gestion_stock": True,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": True,
            "gestion_caisse": True,
            "gestion_fournisseurs": True
        },
        "comptable": {
            "gestion_utilisateurs": False,
            "gestion_stock": False,
            "gestion_ventes": True,
            "gestion_clients": True,
            "rapports": True,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": False
        },
        "preparateur": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": False,
            "gestion_clients": False,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": True
        },
        "stockiste": {
            "gestion_utilisateurs": False,
            "gestion_stock": True,
            "gestion_ventes": False,
            "gestion_clients": False,
            "rapports": False,
            "configuration": False,
            "gestion_caisse": False,
            "gestion_fournisseurs": True
        }
    }
    
    return permissions.get(role, {})


# =========================
# ENDPOINTS SPÉCIAUX (DOIVENT ÊTRE AVANT LES ROUTES AVEC PARAMÈTRES)
# =========================

@router.get("/online-users", status_code=status.HTTP_200_OK)
def get_all_online_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère tous les utilisateurs en ligne du tenant
    """
    if current_user.role not in ["admin", "super_admin", "gestionnaire"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    # Calculer le seuil d'inactivité (15 minutes)
    threshold = datetime.utcnow() - timedelta(minutes=15)
    
    online_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True,
        User.last_login >= threshold
    ).all()
    
    result = []
    for user in online_users:
        if user.last_login:
            login_duration = datetime.utcnow() - user.last_login
            duration_minutes = int(login_duration.total_seconds() / 60)
            
            # Déterminer le statut
            if duration_minutes < 5:
                status = "online"
            elif duration_minutes < 15:
                status = "idle"
            else:
                status = "away"
        else:
            duration_minutes = 0
            status = "away"
        
        result.append({
            "id": str(user.id),
            "nom_complet": user.nom_complet,
            "email": user.email,
            "role": user.role,
            "last_login": user.last_login.isoformat() if user.last_login else None,
            "login_duration": f"{duration_minutes} min",
            "status": status,
            "active_pharmacy_id": str(user.active_pharmacy_id) if user.active_pharmacy_id else None,
            "active_branch_id": str(user.active_branch_id) if user.active_branch_id else None
        })
    
    return {
        "tenant_id": str(current_user.tenant_id),
        "online_count": len(online_users),
        "users": result,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/me/profile", status_code=status.HTTP_200_OK)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère le profil de l'utilisateur connecté.
    """
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    
    profile = current_user.to_dict(include_tenant=False, include_pharmacies=True)
    if tenant:
        profile["pharmacie"] = {
            "id": str(tenant.id),
            "nom": getattr(tenant, 'nom_pharmacie', 'N/A'),
            "ville": getattr(tenant, 'ville', 'N/A'),
            "adresse": getattr(tenant, 'adresse', 'N/A')
        }
    else:
        profile["pharmacie"] = None
    
    return profile


@router.post("/me/change-password", status_code=status.HTTP_200_OK)
def change_my_password(
    request_data: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Permet à un utilisateur de changer son propre mot de passe.
    """
    old_password = request_data.old_password
    new_password = request_data.new_password
    
    # Vérifier l'ancien mot de passe
    if not verify_password(old_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ancien mot de passe incorrect"
        )
    
    # Vérifier que le nouveau mot de passe est différent
    if verify_password(new_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le nouveau mot de passe doit être différent de l'ancien"
        )
    
    # Vérifier la longueur du mot de passe
    if len(new_password) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mot de passe doit contenir au moins 8 caractères"
        )
    
    try:
        # Mettre à jour le mot de passe
        current_user.password_hash = hash_password(new_password)
        db.commit()
        
        # Audit log
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="CHANGE_PASSWORD",
            cible="user",
            description=f"Changement de mot de passe pour: {current_user.email}",
            ip=None
        )
        
        return {
            "message": "Mot de passe changé avec succès",
            "details": "Vous serez déconnecté à votre prochaine connexion"
        }
    
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur changement mot de passe: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du changement de mot de passe: {str(e)}"
        )


# =========================
# ENDPOINTS GÉNÉRAUX
# =========================

@router.get("/", status_code=status.HTTP_200_OK)
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    actif: Optional[bool] = Query(None)
):
    """
    Liste les utilisateurs du tenant avec pagination et filtres.
    """
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Seuls les administrateurs peuvent voir la liste des utilisateurs."
        )

    # Construire la requête
    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)
    
    # Appliquer les filtres
    if search:
        search_term = f"%{search.lower()}%"
        query = query.filter(
            (User.nom_complet.ilike(search_term)) |
            (User.email.ilike(search_term)) |
            (User.telephone.ilike(search_term))
        )
    
    if role:
        query = query.filter(User.role == role)
    
    if actif is not None:
        query = query.filter(User.actif == actif)

    # Pagination
    total = query.count()
    offset = (page - 1) * limit
    users = query.order_by(User.date_creation.desc()).offset(offset).limit(limit).all()
    
    # Format de réponse
    result = []
    for user in users:
        user_dict = user.to_dict(include_tenant=False, include_pharmacies=True)
        # Ajouter des informations supplémentaires
        user_dict["can_edit"] = (
            current_user.id != user.id and
            current_user.role in ["admin", "super_admin"]
        )
        result.append(user_dict)

    return result


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un utilisateur pour le tenant de l'admin connecté.
    Accepte 'full_name' du frontend et le convertit en 'nom_complet'.
    Optionnellement associe l'utilisateur à une pharmacie.
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Accès refusé. Seuls les administrateurs peuvent créer des utilisateurs."
        )

    # Vérifier que l'admin est actif
    if not current_user.actif:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre compte est désactivé"
        )

    # Vérifier si le tenant existe
    tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant non trouvé"
        )
    
    # Vérifier l'unicité de l'email dans le tenant
    existing_user = db.query(User).filter(
        User.email == user_data.email.lower().strip(),
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cet email est déjà utilisé dans votre pharmacie"
        )

    # Vérifier les limites du plan
    user_count = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True
    ).count()
    
    max_users = 10  # Valeur par défaut
    if hasattr(tenant, 'max_users') and tenant.max_users:
        max_users = tenant.max_users
    
    if user_count >= max_users:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Limite d'utilisateurs atteinte ({max_users} maximum). Veuillez mettre à jour votre plan."
        )

    # Vérifier les rôles autorisés
    allowed_roles = ["pharmacien", "vendeur", "caissier", "gestionnaire", "comptable", "preparateur", "stockiste"]
    if current_user.role != "super_admin":
        allowed_roles = [role for role in allowed_roles if role != "admin"]
    
    if user_data.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Rôle non autorisé. Rôles autorisés: {', '.join(allowed_roles)}"
        )

    # Si une pharmacie est fournie, vérifier qu'elle existe et appartient au tenant
    pharmacy = None
    if user_data.pharmacy_id:
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == user_data.pharmacy_id,
            Pharmacy.tenant_id == current_user.tenant_id
        ).first()
        
        if not pharmacy:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pharmacie non trouvée"
            )

    try:
        # Création de l'utilisateur
        new_user = User(
            tenant_id=current_user.tenant_id,
            nom_complet=user_data.full_name,
            email=user_data.email.lower().strip(),
            password_hash=hash_password(user_data.password),
            role=user_data.role,
            actif=user_data.is_active if user_data.is_active is not None else True,
            telephone=user_data.telephone,
            adresse=user_data.adresse,
            permissions=user_data.permissions or get_default_permissions(user_data.role)
        )
        
        db.add(new_user)
        db.flush()  # Pour obtenir l'ID du nouvel utilisateur
        
        # Associer l'utilisateur à la pharmacie si fournie
        if pharmacy:
            user_pharmacy = UserPharmacy(
                user_id=new_user.id,
                pharmacy_id=pharmacy.id,
                is_primary=True,  # Par défaut, c'est la pharmacie principale
                can_manage=(user_data.role in ["admin", "gestionnaire"])
            )
            db.add(user_pharmacy)
            
            # Définir la pharmacie active
            new_user.active_pharmacy_id = pharmacy.id
            
            # Si une branche est fournie, la définir comme active
            if user_data.branch_id:
                new_user.active_branch_id = user_data.branch_id
        
        db.commit()
        db.refresh(new_user)

        # Audit log
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="CREATE_USER",
            cible="user",
            description=f"Création utilisateur: {new_user.email} (role={new_user.role})",
            ip=request.client.host if request.client else None
        )

        # Récupérer le nom de la pharmacie
        pharmacie_nom = getattr(tenant, 'nom_pharmacie', 'Votre pharmacie')
        pharmacie_nom = pharmacie_nom if pharmacie_nom else 'Votre pharmacie'

        return {
            "message": "Utilisateur créé avec succès",
            "user": {
                "id": str(new_user.id),
                "email": new_user.email,
                "nom_complet": new_user.nom_complet,
                "role": new_user.role,
                "telephone": new_user.telephone,
                "actif": new_user.actif,
                "date_creation": new_user.date_creation.isoformat() if new_user.date_creation else None,
                "active_pharmacy_id": str(new_user.active_pharmacy_id) if new_user.active_pharmacy_id else None,
                "active_branch_id": str(new_user.active_branch_id) if new_user.active_branch_id else None,
                "pharmacie": pharmacie_nom
            },
            "instructions": f"L'utilisateur peut se connecter à votre pharmacie '{pharmacie_nom}' avec son email et mot de passe"
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création utilisateur: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création de l'utilisateur: {str(e)}"
        )


# =========================
# ENDPOINTS PAR USER_ID
# =========================

@router.get("/{user_id}", status_code=status.HTTP_200_OK)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les détails d'un utilisateur spécifique du tenant.
    """
    # Vérifier si c'est un UUID valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )
    
    # Les admins peuvent voir tous les utilisateurs, les autres seulement leur propre profil
    if current_user.role not in ["admin", "super_admin"] and str(current_user.id) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez voir que votre propre profil"
        )

    # Si c'est un admin, vérifier que l'utilisateur est dans le même tenant
    if current_user.role in ["admin", "super_admin"]:
        user = db.query(User).filter(
            User.id == user_id,
            User.tenant_id == current_user.tenant_id
        ).first()
    else:
        # L'utilisateur voit son propre profil
        user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable"
        )

    return user.to_dict(include_tenant=False, include_pharmacies=True)


@router.put("/{user_id}", status_code=status.HTTP_200_OK)
def update_user(
    user_id: str,
    user_data: UserUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Met à jour un utilisateur du tenant courant.
    """
    # Vérifier les permissions
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    # Vérifier que c'est un UUID valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    # Récupérer l'utilisateur à modifier
    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    # Vérifier qu'on ne modifie pas soi-même si on se désactive
    if user.id == current_user.id and user_data.actif == False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas désactiver votre propre compte"
        )

    # Vérifier l'unicité de l'email si modifié
    if user_data.email and user_data.email != user.email:
        existing_user = db.query(User).filter(
            User.email == user_data.email.lower().strip(),
            User.tenant_id == current_user.tenant_id,
            User.id != user_id
        ).first()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cet email est déjà utilisé dans votre pharmacie"
            )

    # Vérifier les rôles si modification
    if user_data.role and user_data.role != user.role:
        allowed_roles = ["pharmacien", "vendeur", "caissier", "gestionnaire", "comptable", "preparateur", "stockiste"]
        if current_user.role != "super_admin":
            allowed_roles = [role for role in allowed_roles if role != "admin"]
        
        if user_data.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Rôle non autorisé. Rôles autorisés: {', '.join(allowed_roles)}"
            )

    # Vérifier la pharmacie active si elle est modifiée
    if user_data.active_pharmacy_id:
        # Vérifier que l'utilisateur a accès à cette pharmacie
        if not user.has_access_to_pharmacy(user_data.active_pharmacy_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="L'utilisateur n'a pas accès à cette pharmacie"
            )

    try:
        # Mise à jour des champs
        if user_data.nom_complet is not None:
            user.nom_complet = user_data.nom_complet
            
        if user_data.email is not None:
            user.email = user_data.email.lower().strip()
            
        if user_data.telephone is not None:
            user.telephone = user_data.telephone
            
        if user_data.adresse is not None:
            user.adresse = user_data.adresse
            
        if user_data.role is not None:
            user.role = user_data.role
            # Mettre à jour les permissions si le rôle change
            user.permissions = get_default_permissions(user_data.role)
            
        if user_data.actif is not None:
            user.actif = user_data.actif
            
        if user_data.password is not None:
            user.password_hash = hash_password(user_data.password)
            
        if user_data.active_pharmacy_id is not None:
            user.active_pharmacy_id = user_data.active_pharmacy_id
            
        if user_data.active_branch_id is not None:
            user.active_branch_id = user_data.active_branch_id

        db.commit()
        db.refresh(user)

        # Audit log
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="UPDATE_USER",
            cible="user",
            description=f"Mise à jour utilisateur: {user.email}",
            ip=request.client.host if request.client else None
        )

        return {
            "message": "Utilisateur mis à jour avec succès",
            "user": user.to_dict(include_tenant=False, include_pharmacies=True)
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur mise à jour utilisateur {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la mise à jour: {str(e)}"
        )


@router.patch("/{user_id}/toggle", status_code=status.HTTP_200_OK)
def toggle_user(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Active ou désactive un utilisateur.
    """
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    # Vérifier que c'est un UUID valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    # Empêcher de se désactiver soi-même
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas modifier votre propre statut"
        )

    try:
        # Basculer l'état
        user.actif = not user.actif
        db.commit()
        db.refresh(user)

        # Log d'audit
        action = "ACTIVATE_USER" if user.actif else "DEACTIVATE_USER"
        status_text = "activé" if user.actif else "désactivé"
        
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action=action,
            cible="user",
            description=f"Utilisateur {status_text}: {user.email}",
            ip=request.client.host if request.client else None
        )

        return {
            "message": f"Utilisateur {status_text} avec succès",
            "user": user.to_dict(include_tenant=False, include_pharmacies=True)
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur toggle utilisateur {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la modification du statut: {str(e)}"
        )


@router.post("/{user_id}/reset-password", status_code=status.HTTP_200_OK)
def reset_user_password(
    user_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Réinitialise le mot de passe d'un utilisateur (admin seulement).
    """
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )

    # Vérifier que c'est un UUID valide
    if not is_valid_uuid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format d'ID utilisateur invalide"
        )

    user = db.query(User).filter(
        User.id == user_id,
        User.tenant_id == current_user.tenant_id
    ).first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Utilisateur introuvable dans votre pharmacie"
        )

    # Générer un mot de passe temporaire
    import secrets
    import string
    
    alphabet = string.ascii_letters + string.digits
    temp_password = ''.join(secrets.choice(alphabet) for _ in range(10))
    
    try:
        # Mettre à jour le mot de passe
        user.password_hash = hash_password(temp_password)
        db.commit()

        # Audit log
        log_action(
            db=db,
            tenant_id=current_user.tenant_id,
            user_id=current_user.id,
            action="RESET_USER_PASSWORD",
            cible="user",
            description=f"Réinitialisation mot de passe pour: {user.email}",
            ip=request.client.host if request.client else None
        )

        return {
            "message": "Mot de passe réinitialisé avec succès",
            "temporary_password": temp_password,
            "instructions": "Communiquez ce mot de passe temporaire à l'utilisateur. Il devra le changer à sa première connexion.",
            "user": {
                "id": str(user.id),
                "email": user.email,
                "nom_complet": user.nom_complet
            }
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Erreur réinitialisation mot de passe {user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la réinitialisation: {str(e)}"
        )


# Récupérer tous les users (super_admin seulement)
@router.get("/all", status_code=status.HTTP_200_OK)
def get_all_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100)
):
    """Liste TOUS les utilisateurs (super_admin seulement)"""
    if current_user.role != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    users = db.query(User).order_by(User.date_creation.desc()).offset((page-1)*limit).limit(limit).all()
    return [user.to_dict(include_tenant=True, include_pharmacies=True) for user in users]


# =========================
# STATISTIQUES ET HEALTH
# =========================

@router.get("/statistics/overview", status_code=status.HTTP_200_OK)
def get_user_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Retourne des statistiques sur les utilisateurs du tenant.
    """
    if current_user.role not in ["admin", "super_admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé"
        )
    
    # Compter les utilisateurs par statut
    total = db.query(User).filter(User.tenant_id == current_user.tenant_id).count()
    active = db.query(User).filter(
        User.tenant_id == current_user.tenant_id,
        User.actif == True
    ).count()
    inactive = total - active
    
    # Compter par rôle
    roles = db.query(User.role, func.count(User.id)).filter(
        User.tenant_id == current_user.tenant_id
    ).group_by(User.role).all()
    
    role_distribution = {role: count for role, count in roles}
    
    # Derniers utilisateurs créés
    recent_users = db.query(User).filter(
        User.tenant_id == current_user.tenant_id
    ).order_by(User.date_creation.desc()).limit(5).all()
    
    recent_users_data = [{
        "id": str(u.id),
        "nom_complet": u.nom_complet,
        "email": u.email,
        "role": u.role,
        "date_creation": u.date_creation.isoformat() if u.date_creation else None
    } for u in recent_users]
    
    return {
        "total_users": total,
        "active_users": active,
        "inactive_users": inactive,
        "role_distribution": role_distribution,
        "recent_users": recent_users_data,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """
    Endpoint de vérification de santé de l'API users.
    """
    return {
        "status": "healthy",
        "service": "users-api",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": [
            "POST /users/ - Créer un utilisateur",
            "GET /users/ - Lister les utilisateurs",
            "GET /users/online-users - Utilisateurs en ligne",
            "GET /users/{id} - Voir un utilisateur",
            "PUT /users/{id} - Modifier un utilisateur",
            "PATCH /users/{id}/toggle - Activer/désactiver",
            "POST /users/{id}/reset-password - Réinitialiser mot de passe",
            "GET /users/me/profile - Mon profil",
            "POST /users/me/change-password - Changer mon mot de passe",
            "GET /users/statistics/overview - Statistiques"
        ]
    }