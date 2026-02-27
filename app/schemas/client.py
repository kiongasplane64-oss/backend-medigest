"""
app/schemas/client.py
Schémas Pydantic pour les clients
"""

from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, List, Dict, Any
from datetime import datetime, date
from enum import Enum
from uuid import UUID


class ClientType(str, Enum):
    """Types de clients"""
    PARTICULIER = "particulier"
    PROFESSIONNEL = "professionnel"
    ASSUREUR = "assureur"
    ETAT = "etat"
    HOPITAL = "hopital"
    CLINIQUE = "clinique"


class ClientBase(BaseModel):
    """Base schema pour les clients"""
    nom: str = Field(..., min_length=2, max_length=100, description="Nom complet du client")
    telephone: Optional[str] = Field(None, max_length=20, description="Numéro de téléphone")
    email: Optional[EmailStr] = Field(None, description="Adresse email")
    adresse: Optional[str] = Field(None, max_length=200, description="Adresse postale")
    ville: Optional[str] = Field(None, max_length=50, description="Ville")
    type_client: Optional[ClientType] = Field(ClientType.PARTICULIER, description="Type de client")
    
    # Informations légales (pour professionnels)
    entreprise: Optional[str] = Field(None, max_length=100, description="Nom de l'entreprise")
    num_contribuable: Optional[str] = Field(None, max_length=50, description="Numéro de contribuable")
    rccm: Optional[str] = Field(None, max_length=50, description="Numéro RCCM")
    id_nat: Optional[str] = Field(None, max_length=50, description="Identifiant national")
    
    # Crédit
    credit_limit: Optional[float] = Field(0.0, ge=0, description="Limite de crédit")
    eligible_credit: Optional[bool] = Field(False, description="Éligible au crédit")
    
    # Notes et préférences
    notes: Optional[str] = Field(None, description="Notes internes")
    preferences: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Préférences du client")


class ClientCreate(ClientBase):
    """Schema pour la création d'un client"""
    telephone: str = Field(..., max_length=20, description="Numéro de téléphone requis")
    
    @validator("telephone")
    def validate_phone(cls, v):
        # Nettoyer le numéro
        v = ''.join(c for c in v if c.isdigit())
        
        if not v:
            raise ValueError("Numéro de téléphone invalide")
        
        # Pour la RDC, vérifier la longueur
        if len(v) == 9:
            # Numéro local congolais (ex: 820581234)
            return v
        elif len(v) == 11 and v.startswith('243'):
            # Numéro avec indicatif congolais
            return v
        elif len(v) == 12 and v.startswith('243'):
            # Peut-être avec un 0 au début
            return v[1:] if v[0] == '0' else v
        else:
            # Accepte d'autres formats internationaux
            if len(v) < 9 or len(v) > 15:
                raise ValueError("Numéro de téléphone invalide")
            return v


class ClientUpdate(BaseModel):
    """Schema pour la mise à jour d'un client"""
    nom: Optional[str] = Field(None, min_length=2, max_length=100)
    telephone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    adresse: Optional[str] = Field(None, max_length=200)
    ville: Optional[str] = Field(None, max_length=50)
    type_client: Optional[ClientType] = None
    entreprise: Optional[str] = Field(None, max_length=100)
    num_contribuable: Optional[str] = Field(None, max_length=50)
    rccm: Optional[str] = Field(None, max_length=50)
    id_nat: Optional[str] = Field(None, max_length=50)
    credit_limit: Optional[float] = Field(None, ge=0)
    eligible_credit: Optional[bool] = None
    notes: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None
    blacklisted: Optional[bool] = None
    blacklist_reason: Optional[str] = None


class ClientInDB(ClientBase):
    """Schema pour un client retourné par l'API"""
    id: UUID
    tenant_id: UUID
    credit_score: Optional[int]
    dette_actuelle: float
    total_achats: float
    nombre_achats: int
    moyenne_achat: float
    date_inscription: Optional[datetime]
    dernier_achat: Optional[datetime]
    date_dernier_paiement: Optional[datetime]
    is_active: bool
    blacklisted: bool
    blacklist_reason: Optional[str]
    client_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    # Propriétés calculées
    credit_available: float
    days_since_last_purchase: Optional[int]
    
    class Config:
        from_attributes = True


class ClientStats(BaseModel):
    """Statistiques détaillées d'un client"""
    client_id: UUID
    nom: str
    total_achats: float
    nombre_achats: int
    moyenne_achat: float
    credit_limit: float
    dette_actuelle: float
    credit_available: float
    credit_score: Optional[int]
    credit_utilization: float
    credit_status: str  # normal, warning, critical, clean
    days_since_last_purchase: Optional[int]
    last_payment_date: Optional[datetime]
    days_since_last_payment: Optional[int]
    eligible_credit: bool
    blacklisted: bool


class ClientSearchResult(BaseModel):
    """Résultat de recherche de client"""
    id: UUID
    nom: str
    telephone: Optional[str]
    email: Optional[str]
    entreprise: Optional[str]
    type_client: str
    dette_actuelle: float
    credit_available: float


class ClientDebtInfo(BaseModel):
    """Informations de dette d'un client"""
    client_id: UUID
    nom: str
    credit_limit: float
    dette_actuelle: float
    credit_available: float
    credit_utilization: float
    eligible_credit: bool
    last_payment_date: Optional[datetime]
    risk_level: str  # low, medium, high
    debts_history: List[Dict[str, Any]]
    total_paid: float
    pending_debts_count: int


class ClientSummary(BaseModel):
    """Résumé des clients"""
    total_clients: int
    clients_with_credit: int
    blacklisted_clients: int
    total_debt: float
    total_sales: float
    clients_by_type: List[Dict[str, Any]]
    top_clients: List[Dict[str, Any]]