# app/api/v1/transfers.py

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from sqlalchemy import desc, or_
from sqlalchemy.orm import Session, joinedload

from app.api import deps
from app.models.tenant import Tenant
from app.models.user import User
from app.models.transfert import (
    ProductTransfer,
    TransferItem,
    TransferPriority,
    TransferStatus,
    TransferType,
)
from app.schemas.transfer import (
    TransferApprove,
    TransferCancel,
    TransferCreate,
    TransferInDB,
    TransferListResponse,
    TransferReceive,
    TransferShip,
    TransferStatistics,
    TransferUpdate,
)
from app.services.transfer_service import TransferService

router = APIRouter()


# ============================================================================
# ROUTES SPÉCIFIQUES (doivent être définies AVANT les routes dynamiques)
# ============================================================================

@router.get("/pending", response_model=List[TransferInDB])
def get_pending_transfers(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
):
    """
    Récupère les transferts en attente pour une pharmacie.
    """
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )

    transfers = (
        db.query(ProductTransfer)
        .filter(
            ProductTransfer.tenant_id == current_tenant.id,
            ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
            ProductTransfer.status == TransferStatus.PENDING,
        )
        .options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.items),
        )
        .order_by(desc(ProductTransfer.created_at))
        .all()
    )

    return transfers


@router.get("/statistics/summary", response_model=TransferStatistics)
def get_transfer_statistics(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
):
    """
    Récupère les statistiques des transferts.
    """
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )

    incoming = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
    )

    outgoing = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        ProductTransfer.source_pharmacy_id == query_pharmacy_id,
    )

    completed_values = db.query(ProductTransfer.total_value).filter(
        ProductTransfer.tenant_id == current_tenant.id,
        or_(
            ProductTransfer.source_pharmacy_id == query_pharmacy_id,
            ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
        ),
        ProductTransfer.status == TransferStatus.COMPLETED,
    ).all()

    total_value_transferred_sum = 0.0
    for row in completed_values:
        value = row[0] if isinstance(row, tuple) else row
        if value is not None:
            total_value_transferred_sum += float(value)

    return TransferStatistics(
        pending_incoming=incoming.filter(
            ProductTransfer.status == TransferStatus.PENDING
        ).count(),
        pending_outgoing=outgoing.filter(
            ProductTransfer.status == TransferStatus.PENDING
        ).count(),
        in_transit_incoming=incoming.filter(
            ProductTransfer.status == TransferStatus.IN_TRANSIT
        ).count(),
        in_transit_outgoing=outgoing.filter(
            ProductTransfer.status == TransferStatus.IN_TRANSIT
        ).count(),
        completed_this_month=db.query(ProductTransfer).filter(
            ProductTransfer.tenant_id == current_tenant.id,
            or_(
                ProductTransfer.source_pharmacy_id == query_pharmacy_id,
                ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
            ),
            ProductTransfer.status == TransferStatus.COMPLETED,
            ProductTransfer.completed_date >= datetime.now().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ),
        ).count(),
        total_value_transferred_sum=total_value_transferred_sum,
    )


# ============================================================================
# ROUTES PRINCIPALES
# ============================================================================

@router.get("/", response_model=TransferListResponse)
def get_transfers(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    pharmacy_id: Optional[UUID] = Query(None, description="ID de la pharmacie"),
    status_filter: Optional[TransferStatus] = Query(
        None, alias="status", description="Statut du transfert"
    ),
    transfer_type: Optional[TransferType] = Query(
        None, description="Type de transfert"
    ),
    priority: Optional[TransferPriority] = Query(
        None, description="Priorité"
    ),
    direction: Optional[str] = Query(
        None, description="Direction: incoming, outgoing, all"
    ),
    start_date: Optional[datetime] = Query(
        None, description="Date de début"
    ),
    end_date: Optional[datetime] = Query(
        None, description="Date de fin"
    ),
    search: Optional[str] = Query(
        None, description="Recherche par numéro ou produit"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Récupère la liste des transferts.
    """
    query_pharmacy_id = _validate_pharmacy_access(
        current_user, current_tenant.id, pharmacy_id
    )

    query = db.query(ProductTransfer).filter(
        ProductTransfer.tenant_id == current_tenant.id
    )

    # Filtre par direction
    if direction == "incoming":
        query = query.filter(
            ProductTransfer.destination_pharmacy_id == query_pharmacy_id
        )
    elif direction == "outgoing":
        query = query.filter(
            ProductTransfer.source_pharmacy_id == query_pharmacy_id
        )
    else:
        query = query.filter(
            or_(
                ProductTransfer.source_pharmacy_id == query_pharmacy_id,
                ProductTransfer.destination_pharmacy_id == query_pharmacy_id,
            )
        )

    # Filtres optionnels
    if status_filter is not None:
        query = query.filter(ProductTransfer.status == status_filter)

    if transfer_type is not None:
        query = query.filter(ProductTransfer.transfer_type == transfer_type)

    if priority is not None:
        query = query.filter(ProductTransfer.priority == priority)

    if start_date is not None:
        query = query.filter(ProductTransfer.requested_date >= start_date)

    if end_date is not None:
        query = query.filter(ProductTransfer.requested_date <= end_date)

    if search:
        query = query.filter(
            or_(
                ProductTransfer.transfer_number.ilike(f"%{search}%"),
                ProductTransfer.items.any(
                    TransferItem.product_name.ilike(f"%{search}%")
                ),
            )
        )

    total = query.count()

    transfers = (
        query.options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.approved_by),
            joinedload(ProductTransfer.prepared_by),
            joinedload(ProductTransfer.shipped_by),
            joinedload(ProductTransfer.received_by),
            joinedload(ProductTransfer.cancelled_by),
            joinedload(ProductTransfer.items).joinedload(TransferItem.product),
        )
        .order_by(desc(ProductTransfer.created_at))
        .offset(skip)
        .limit(limit)
        .all()
    )

    return TransferListResponse(
        transfers=transfers,
        total=total,
        skip=skip,
        limit=limit,
    )


@router.post("/", response_model=TransferInDB, status_code=status.HTTP_201_CREATED)
def create_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_data: TransferCreate,
):
    """
    Crée un nouveau transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.create_transfer(transfer_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


# ============================================================================
# ROUTES AVEC PARAMÈTRES DYNAMIQUES
# ============================================================================

@router.get("/{transfer_id}", response_model=TransferInDB)
def get_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
):
    """
    Récupère un transfert par son ID.
    """
    transfer = (
        db.query(ProductTransfer)
        .filter(
            ProductTransfer.id == transfer_id,
            ProductTransfer.tenant_id == current_tenant.id,
        )
        .options(
            joinedload(ProductTransfer.source_pharmacy),
            joinedload(ProductTransfer.destination_pharmacy),
            joinedload(ProductTransfer.requested_by),
            joinedload(ProductTransfer.approved_by),
            joinedload(ProductTransfer.prepared_by),
            joinedload(ProductTransfer.shipped_by),
            joinedload(ProductTransfer.received_by),
            joinedload(ProductTransfer.cancelled_by),
            joinedload(ProductTransfer.items).joinedload(TransferItem.product),
        )
        .first()
    )

    if not transfer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Transfert non trouvé",
        )

    # Vérifier l'accès à la pharmacie
    user_pharmacy_id = _get_current_user_pharmacy_id(current_user)
    
    if (
        transfer.source_pharmacy_id != user_pharmacy_id
        and transfer.destination_pharmacy_id != user_pharmacy_id
        and not _is_super_admin(current_user)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'avez pas accès à ce transfert",
        )

    return transfer


@router.patch("/{transfer_id}", response_model=TransferInDB)
def update_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    transfer_data: TransferUpdate,
):
    """
    Met à jour un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.update_transfer(transfer_id, transfer_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/approve", response_model=TransferInDB)
def approve_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    approve_data: TransferApprove,
):
    """
    Approuve un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.approve_transfer(transfer_id, approve_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/prepare", response_model=TransferInDB)
def prepare_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
):
    """
    Prépare un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.prepare_transfer(transfer_id)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/ship", response_model=TransferInDB)
def ship_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    ship_data: TransferShip,
):
    """
    Expédie un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.ship_transfer(transfer_id, ship_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/receive", response_model=TransferInDB)
def receive_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    receive_data: TransferReceive,
):
    """
    Enregistre la réception d'un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.receive_transfer(transfer_id, receive_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


@router.post("/{transfer_id}/cancel", response_model=TransferInDB)
def cancel_transfer(
    *,
    db: Session = Depends(deps.get_db),
    current_tenant: Tenant = Depends(deps.get_current_tenant),
    current_user: User = Depends(deps.get_current_user),
    transfer_id: UUID = Path(..., description="ID du transfert"),
    cancel_data: TransferCancel,
):
    """
    Annule un transfert.
    """
    service = TransferService(db, current_user, current_tenant)

    try:
        return service.cancel_transfer(transfer_id, cancel_data)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e


# ============================================================================
# FONCTIONS UTILITAIRES (helpers)
# ============================================================================

def _get_current_user_pharmacy_id(current_user: User) -> UUID:
    """
    Retourne l'ID de la pharmacie principale de l'utilisateur.
    """
    primary_pharmacy = current_user.get_primary_pharmacy()

    if not primary_pharmacy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune pharmacie associée à l'utilisateur",
        )

    return primary_pharmacy.id


def _is_super_admin(user: User) -> bool:
    """
    Vérifie si l'utilisateur est super admin.
    """
    return bool(getattr(user, "is_super_admin", False))


def _validate_pharmacy_access(
    current_user: User,
    tenant_id: UUID,
    requested_pharmacy_id: Optional[UUID],
) -> UUID:
    """
    Détermine la pharmacie à utiliser et vérifie les droits d'accès.
    """
    user_pharmacy_id = _get_current_user_pharmacy_id(current_user)
    query_pharmacy_id = requested_pharmacy_id or user_pharmacy_id

    if (
        requested_pharmacy_id
        and requested_pharmacy_id != user_pharmacy_id
        and not _is_super_admin(current_user)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous n'avez pas accès aux transferts de cette pharmacie",
        )

    return query_pharmacy_id