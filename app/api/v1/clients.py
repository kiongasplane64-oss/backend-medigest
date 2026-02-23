"""
app/api/v1/clients.py
Routes API pour la gestion des clients
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from uuid import UUID
import logging

from app.db.session import get_db
from app.models.client import Client
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.client import (
    ClientCreate, ClientUpdate, ClientInDB,
    ClientStats, ClientSearchResult, ClientDebtInfo
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission

router = APIRouter(prefix="/clients", tags=["Clients"])
logger = logging.getLogger(__name__)


@router.get("/", response_model=List[ClientInDB])
@require_permission("client_view")
def list_clients(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    type_client: Optional[str] = None,
    ville: Optional[str] = None,
    eligible_credit: Optional[bool] = None,
    blacklisted: Optional[bool] = None,
    order_by: str = Query("nom", pattern="^(nom|total_achats|dernier_achat|dette_actuelle)$"),
    order_dir: str = Query("asc", pattern="^(asc|desc)$")
):
    """
    Liste les clients avec filtres
    """
    try:
        query = db.query(Client).filter(Client.tenant_id == current_tenant.id)
        
        # Filtres de recherche
        if search:
            search_filter = or_(
                Client.nom.ilike(f"%{search}%"),
                Client.telephone.ilike(f"%{search}%"),
                Client.email.ilike(f"%{search}%"),
                Client.entreprise.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)
        
        if type_client:
            query = query.filter(Client.type_client == type_client)
        
        if ville:
            query = query.filter(Client.ville.ilike(f"%{ville}%"))
        
        if eligible_credit is not None:
            query = query.filter(Client.eligible_credit == eligible_credit)
        
        if blacklisted is not None:
            query = query.filter(Client.blacklisted == blacklisted)
        
        # Tri
        if order_by == "nom":
            order_field = Client.nom
        elif order_by == "total_achats":
            order_field = Client.total_achats
        elif order_by == "dernier_achat":
            order_field = Client.dernier_achat
        elif order_by == "dette_actuelle":
            order_field = Client.dette_actuelle
        
        if order_dir == "desc":
            order_field = desc(order_field)
        
        query = query.order_by(order_field)
        
        # Pagination
        clients = query.offset(skip).limit(limit).all()
        
        return clients
        
    except Exception as e:
        logger.error(f"Erreur lors de la liste des clients: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la récupération des clients"
        )


@router.get("/search", response_model=List[ClientSearchResult])
@require_permission("client_view")
def search_clients(
    q: str = Query(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Recherche rapide de clients
    """
    try:
        query = db.query(Client).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        )
        
        if q:
            search_filter = or_(
                Client.nom.ilike(f"%{q}%"),
                Client.telephone.ilike(f"%{q}%"),
                Client.email.ilike(f"%{q}%"),
                Client.entreprise.ilike(f"%{q}%"),
                Client.num_contribuable.ilike(f"%{q}%")
            )
            query = query.filter(search_filter)
        
        clients = query.limit(20).all()
        
        results = []
        for client in clients:
            results.append(ClientSearchResult(
                id=client.id,
                nom=client.nom,
                telephone=client.telephone,
                email=client.email,
                entreprise=client.entreprise,
                type_client=client.type_client,
                dette_actuelle=float(client.dette_actuelle),
                credit_available=float(client.credit_available)
            ))
        
        return results
        
    except Exception as e:
        logger.error(f"Erreur lors de la recherche de clients: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la recherche"
        )


@router.get("/{client_id}", response_model=ClientInDB)
@require_permission("client_view")
def get_client(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère un client par son ID
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    return client


@router.post("/", response_model=ClientInDB, status_code=status.HTTP_201_CREATED)
@require_permission("client_manage")
def create_client(
    client_data: ClientCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau client
    """
    try:
        # Vérifier si le téléphone existe déjà
        if client_data.telephone:
            existing = db.query(Client).filter(
                Client.tenant_id == current_tenant.id,
                Client.telephone == client_data.telephone
            ).first()
            
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Un client avec ce numéro de téléphone existe déjà"
                )
        
        # Créer le client
        client = Client(
            tenant_id=current_tenant.id,
            nom=client_data.nom,
            telephone=client_data.telephone,
            email=client_data.email,
            adresse=client_data.adresse,
            ville=client_data.ville,
            type_client=client_data.type_client.value if client_data.type_client else "particulier",
            entreprise=client_data.entreprise,
            num_contribuable=client_data.num_contribuable,
            rccm=client_data.rccm,
            id_nat=client_data.id_nat,
            credit_limit=client_data.credit_limit or 0,
            eligible_credit=client_data.eligible_credit or False,
            notes=client_data.notes,
            preferences=client_data.preferences or {},
            is_active=True
        )
        
        db.add(client)
        db.commit()
        db.refresh(client)
        
        logger.info(f"Client créé: {client.nom} ({client.telephone}) par {current_user.nom_complet}")
        
        return client
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du client: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la création du client"
        )


@router.put("/{client_id}", response_model=ClientInDB)
@require_permission("client_manage")
def update_client(
    client_id: UUID,
    client_data: ClientUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Met à jour un client
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    try:
        # Vérifier si le nouveau téléphone existe déjà (pour un autre client)
        if client_data.telephone and client_data.telephone != client.telephone:
            existing = db.query(Client).filter(
                Client.tenant_id == current_tenant.id,
                Client.telephone == client_data.telephone,
                Client.id != client_id
            ).first()
            
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Un autre client avec ce numéro de téléphone existe déjà"
                )
        
        # Mettre à jour les champs
        update_data = client_data.dict(exclude_unset=True)
        
        # Convertir l'enum en string si présent
        if "type_client" in update_data and update_data["type_client"]:
            update_data["type_client"] = update_data["type_client"].value
        
        for field, value in update_data.items():
            if value is not None:
                setattr(client, field, value)
        
        db.commit()
        db.refresh(client)
        
        logger.info(f"Client mis à jour: {client.nom} par {current_user.nom_complet}")
        
        return client
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la mise à jour du client: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la mise à jour du client"
        )


@router.delete("/{client_id}")
@require_permission("client_manage")
def delete_client(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Supprime un client (désactive)
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    try:
        # Vérifier si le client a des dettes
        if client.dette_actuelle > 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de supprimer un client avec des dettes impayées"
            )
        
        # Désactiver au lieu de supprimer (soft delete)
        client.is_active = False
        client.blacklisted = True
        client.blacklist_reason = "Supprimé par l'administrateur"
        
        db.commit()
        
        logger.info(f"Client désactivé: {client.nom} par {current_user.nom_complet}")
        
        return {"message": "Client désactivé avec succès"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la suppression du client: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la suppression du client"
        )


@router.get("/{client_id}/stats", response_model=ClientStats)
@require_permission("client_view")
def get_client_stats(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les statistiques détaillées d'un client
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    try:
        # Calculer des statistiques supplémentaires
        from datetime import datetime
        
        days_since_last_purchase = None
        if client.dernier_achat:
            days_since_last_purchase = (datetime.utcnow() - client.dernier_achat).days
        
        # Vérifier le statut de crédit
        credit_utilization = 0
        if client.credit_limit > 0:
            credit_utilization = (client.dette_actuelle / client.credit_limit) * 100
        
        credit_status = "normal"
        if credit_utilization > 90:
            credit_status = "critical"
        elif credit_utilization > 70:
            credit_status = "warning"
        elif client.dette_actuelle == 0:
            credit_status = "clean"
        
        # Dernières statistiques de paiement
        last_payment_date = client.date_dernier_paiement
        days_since_last_payment = None
        if last_payment_date:
            days_since_last_payment = (datetime.utcnow() - last_payment_date).days
        
        return ClientStats(
            client_id=client.id,
            nom=client.nom,
            total_achats=float(client.total_achats),
            nombre_achats=client.nombre_achats,
            moyenne_achat=float(client.moyenne_achat),
            credit_limit=float(client.credit_limit),
            dette_actuelle=float(client.dette_actuelle),
            credit_available=float(client.credit_available),
            credit_score=client.credit_score,
            credit_utilization=credit_utilization,
            credit_status=credit_status,
            days_since_last_purchase=days_since_last_purchase,
            last_payment_date=last_payment_date,
            days_since_last_payment=days_since_last_payment,
            eligible_credit=client.eligible_credit,
            blacklisted=client.blacklisted
        )
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul des statistiques client: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du calcul des statistiques"
        )


@router.get("/{client_id}/debt-info", response_model=ClientDebtInfo)
@require_permission("client_view")
def get_client_debt_info(
    client_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les informations de dette d'un client
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    try:
        # Récupérer l'historique des dettes (vous devrez adapter selon votre modèle Debt)
        debts_history = []
        total_paid = 0
        pending_debts = 0
        
        # Exemple simplifié - à adapter selon votre modèle
        if hasattr(client, 'debts'):
            for debt in client.debts:
                debts_history.append({
                    "id": debt.id,
                    "amount": float(debt.amount),
                    "due_date": debt.due_date,
                    "status": debt.status,
                    "description": debt.description
                })
                
                if debt.status == "paid":
                    total_paid += float(debt.amount)
                else:
                    pending_debts += 1
        
        # Calculer des indicateurs
        credit_utilization = 0
        if client.credit_limit > 0:
            credit_utilization = (client.dette_actuelle / client.credit_limit) * 100
        
        risk_level = "low"
        if client.dette_actuelle > 0:
            if client.days_since_last_purchase and client.days_since_last_purchase > 90:
                risk_level = "high"
            elif credit_utilization > 80:
                risk_level = "medium"
        
        return ClientDebtInfo(
            client_id=client.id,
            nom=client.nom,
            credit_limit=float(client.credit_limit),
            dette_actuelle=float(client.dette_actuelle),
            credit_available=float(client.credit_available),
            credit_utilization=credit_utilization,
            eligible_credit=client.eligible_credit,
            last_payment_date=client.date_dernier_paiement,
            risk_level=risk_level,
            debts_history=debts_history[:10],  # 10 derniers
            total_paid=total_paid,
            pending_debts_count=pending_debts
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des infos de dette: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de la récupération des informations de dette"
        )


@router.post("/{client_id}/add-debt")
@require_permission("client_manage")
def add_client_debt(
    client_id: UUID,
    amount: float = Query(..., gt=0, description="Montant à ajouter à la dette"),
    reason: str = Query(..., description="Raison de l'ajout de dette"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Ajoute une dette au client
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    if not client.eligible_credit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Client non éligible au crédit"
        )
    
    try:
        # Ajouter la dette
        new_debt = client.dette_actuelle + amount
        
        # Vérifier si la dette dépasse la limite
        if new_debt > client.credit_limit:
            client.dette_actuelle = client.credit_limit
            client.eligible_credit = False
            
            logger.warning(f"Limite de crédit dépassée pour le client {client.nom}")
        else:
            client.dette_actuelle = new_debt
        
        # Log la transaction (vous devriez créer une table pour ça)
        logger.info(f"Dette ajoutée pour {client.nom}: {amount} ({reason}) par {current_user.nom_complet}")
        
        db.commit()
        
        return {
            "message": "Dette ajoutée avec succès",
            "new_debt": float(client.dette_actuelle),
            "credit_available": float(client.credit_available),
            "eligible_credit": client.eligible_credit
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'ajout de dette: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'ajout de dette"
        )


@router.post("/{client_id}/pay-debt")
@require_permission("client_manage")
def pay_client_debt(
    client_id: UUID,
    amount: float = Query(..., gt=0, description="Montant à payer"),
    payment_method: str = Query("cash", pattern="^(cash|card|transfer|mobile)$"),
    reference: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Effectue un paiement sur la dette du client
    """
    client = db.query(Client).filter(
        Client.id == client_id,
        Client.tenant_id == current_tenant.id
    ).first()
    
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Client non trouvé"
        )
    
    if client.dette_actuelle <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le client n'a pas de dette"
        )
    
    if amount > client.dette_actuelle:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le montant dépasse la dette actuelle"
        )
    
    try:
        # Effectuer le paiement
        old_debt = client.dette_actuelle
        client.dette_actuelle -= amount
        client.date_dernier_paiement = datetime.utcnow()
        
        # Réactiver le crédit si la dette est suffisamment réduite
        if client.dette_actuelle <= client.credit_limit * 0.8:
            client.eligible_credit = True
        
        # Log la transaction (à enregistrer dans une table de paiements)
        logger.info(f"Paiement de dette pour {client.nom}: {amount} via {payment_method} par {current_user.nom_complet}")
        
        db.commit()
        
        return {
            "message": "Paiement effectué avec succès",
            "old_debt": float(old_debt),
            "new_debt": float(client.dette_actuelle),
            "amount_paid": amount,
            "remaining_debt": float(client.dette_actuelle),
            "credit_available": float(client.credit_available),
            "eligible_credit": client.eligible_credit
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors du paiement de dette: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du paiement"
        )


@router.get("/stats/summary")
@require_permission("client_view")
def get_clients_summary(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère un résumé des clients
    """
    try:
        # Nombre total de clients
        total_clients = db.query(func.count(Client.id)).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).scalar()
        
        # Clients avec crédit
        clients_with_credit = db.query(func.count(Client.id)).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True,
            Client.eligible_credit == True
        ).scalar()
        
        # Clients blacklistés
        blacklisted_clients = db.query(func.count(Client.id)).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True,
            Client.blacklisted == True
        ).scalar()
        
        # Dette totale
        total_debt = db.query(func.coalesce(func.sum(Client.dette_actuelle), 0)).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).scalar()
        
        # Chiffre d'affaires total
        total_sales = db.query(func.coalesce(func.sum(Client.total_achats), 0)).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).scalar()
        
        # Clients par type
        clients_by_type = db.query(
            Client.type_client,
            func.count(Client.id).label("count")
        ).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).group_by(Client.type_client).all()
        
        # Top clients par chiffre d'affaires
        top_clients = db.query(Client).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).order_by(Client.total_achats.desc()).limit(5).all()
        
        return {
            "total_clients": total_clients,
            "clients_with_credit": clients_with_credit,
            "blacklisted_clients": blacklisted_clients,
            "total_debt": float(total_debt),
            "total_sales": float(total_sales),
            "clients_by_type": [
                {"type": type_client, "count": count}
                for type_client, count in clients_by_type
            ],
            "top_clients": [
                {
                    "id": str(client.id),
                    "nom": client.nom,
                    "total_achats": float(client.total_achats),
                    "nombre_achats": client.nombre_achats
                }
                for client in top_clients
            ]
        }
        
    except Exception as e:
        logger.error(f"Erreur lors du calcul du résumé clients: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors du calcul du résumé"
        )


@router.get("/export/csv")
@require_permission("client_export")
def export_clients_csv(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Exporte la liste des clients en CSV
    """
    try:
        clients = db.query(Client).filter(
            Client.tenant_id == current_tenant.id,
            Client.is_active == True
        ).all()
        
        # Créer le CSV (simplifié)
        csv_lines = [
            "ID;Nom;Téléphone;Email;Entreprise;Type;Adresse;Ville;"
            "Limite crédit;Dette actuelle;Total achats;Nombre achats;"
            "Date inscription;Dernier achat;Blacklisté"
        ]
        
        for client in clients:
            csv_line = f"{client.id};{client.nom};{client.telephone or ''};"
            f"{client.email or ''};{client.entreprise or ''};{client.type_client};"
            f"{client.adresse or ''};{client.ville or ''};"
            f"{float(client.credit_limit)};{float(client.dette_actuelle)};"
            f"{float(client.total_achats)};{client.nombre_achats};"
            f"{client.date_inscription or ''};{client.dernier_achat or ''};"
            f"{'Oui' if client.blacklisted else 'Non'}"
            
            csv_lines.append(csv_line)
        
        csv_content = "\n".join(csv_lines)
        
        return {
            "filename": f"clients_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv",
            "content": csv_content,
            "content_type": "text/csv",
            "client_count": len(clients)
        }
        
    except Exception as e:
        logger.error(f"Erreur lors de l'export CSV: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'export"
        )