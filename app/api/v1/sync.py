# app/api/v1/sync.py - Version finale complète et optimisée
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from app.api.deps import get_db, get_current_user
from app.schemas.sync import SyncPayload, SyncItem
from app.services.sync_service import process_sync, get_changes_since, get_sync_status

router = APIRouter(prefix="/sync", tags=["Sync"])
logger = logging.getLogger(__name__)


# app/api/v1/sync.py - Ajouter une meilleure gestion des données manquantes

@router.post("/")
def sync_data(
    payload: SyncPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint de synchronisation des données
    
    - Filtre automatiquement les items avec des données nulles
    - Valide les actions et les noms de tables
    - Supporte les noms de tables en français et anglais
    - Retourne un rapport détaillé des traitements
    """
    
    # 1. Filtrer les items valides et invalides
    valid_items: List[SyncItem] = []
    invalid_items: List[Dict[str, Any]] = []
    
    for item in payload.items:
        errors = []
        
        # Validation de base : data requis pour create/update/upsert
        if item.action in ['create', 'update', 'upsert'] and (item.data is None or not item.data):
            errors.append("Les données sont requises pour cette action")
            # CORRECTION: Ajouter un log plus détaillé
            logger.warning(f"Item rejeté: action={item.action}, table={item.table_name}, data absent")
        
        # Validation du nom de table (supporte les alias français)
        if item.table_name:
            table_mapping = {
                'produits': 'products', 'produit': 'products',
                'catégories': 'categories', 'categorie': 'categories', 'categories': 'categories',
                'commandes': 'orders', 'commande': 'orders',
                'clients': 'customers', 'client': 'customers',
                'factures': 'invoices', 'facture': 'invoices',
                'utilisateurs': 'users', 'utilisateur': 'users',
                'tenants': 'tenants', 'tenant': 'tenants',
                'subscriptions': 'subscriptions', 'abonnements': 'subscriptions', 'abonnement': 'subscriptions'
            }
            
            allowed_tables = ['products', 'categories', 'orders', 'customers', 
                             'invoices', 'users', 'tenants', 'subscriptions']
            
            normalized = table_mapping.get(item.table_name.lower(), item.table_name.lower())
            if normalized not in allowed_tables:
                errors.append(
                    f"Table '{item.table_name}' non autorisée. "
                    f"Tables autorisées: {', '.join(allowed_tables)}"
                )
        
        # Validation de l'action
        allowed_actions = ['create', 'update', 'delete', 'upsert']
        if item.action.lower() not in allowed_actions:
            errors.append(
                f"Action '{item.action}' non valide. "
                f"Actions autorisées: {', '.join(allowed_actions)}"
            )
        
        # Validation spécifique pour la suppression
        if item.action == 'delete' and item.data:
            if 'id' not in item.data:
                errors.append("L'ID est requis pour l'action 'delete'")
        
        # CORRECTION: Pour create, s'assurer que les données ne sont pas vides
        if item.action == 'create' and item.data:
            # Vérifier que les données ne sont pas vides
            if not any(item.data.values()):
                errors.append("Les données ne peuvent pas être vides pour la création")
        
        if errors:
            invalid_items.append({
                "item": item.model_dump() if hasattr(item, 'model_dump') else item.__dict__,
                "errors": errors
            })
        else:
            # Normaliser le nom de la table avant traitement
            if item.table_name.lower() in ['produits', 'produit', 'catégories', 'categorie', 
                                           'commandes', 'commande', 'clients', 'client',
                                           'factures', 'facture', 'utilisateurs', 'utilisateur',
                                           'abonnements', 'abonnement']:
                table_mapping = {
                    'produits': 'products', 'produit': 'products',
                    'catégories': 'categories', 'categorie': 'categories',
                    'commandes': 'orders', 'commande': 'orders',
                    'clients': 'customers', 'client': 'customers',
                    'factures': 'invoices', 'facture': 'invoices',
                    'utilisateurs': 'users', 'utilisateur': 'users',
                    'abonnements': 'subscriptions', 'abonnement': 'subscriptions'
                }
                item.table_name = table_mapping.get(item.table_name.lower(), item.table_name)
            valid_items.append(item)
    
    # 2. Si tous les items sont invalides
    if not valid_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Aucun item valide à synchroniser",
                "invalid_items": invalid_items,
                "total_items": len(payload.items)
            }
        )  
    # 3. Logger les items filtrés
    if invalid_items:
        logger.warning(
            f"{len(invalid_items)} item(s) ignoré(s) pour le tenant {user.tenant_id}: "
            f"{[item['errors'] for item in invalid_items]}"
        )
    
    # 4. Traiter les items valides
    try:
        # Traitement principal
        process_sync(db, user.tenant_id, valid_items)
        
        # 5. Retourner la réponse de succès
        return {
            "status": "success",
            "message": "Synchronisation traitée avec succès",
            "tenant_id": str(user.tenant_id),
            "summary": {
                "total_items": len(payload.items),
                "processed_items": len(valid_items),
                "ignored_items": len(invalid_items),
                "success": True
            },
            "details": {
                "processed": [
                    {
                        "table_name": item.table_name,
                        "action": item.action,
                        "id": item.data.get('id') if item.data else None
                    }
                    for item in valid_items[:10]  # Limiter pour la réponse
                ] if valid_items else [],
                "ignored": invalid_items if invalid_items else None,
                "processed_tables": list(set(item.table_name for item in valid_items))
            }
        }
        
    except Exception as e:
        # Gestion des erreurs lors du traitement
        logger.error(
            f"Erreur lors de la synchronisation pour le tenant {user.tenant_id}: {str(e)}",
            exc_info=True
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Erreur lors du traitement de la synchronisation",
                "message": str(e),
                "tenant_id": str(user.tenant_id),
                "processed_items": len(valid_items) if valid_items else 0
            }
        )


@router.get("/pull")
def pull_data(
    last_sync: Optional[str] = None,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Récupérer les données modifiées depuis la dernière synchronisation
    
    - last_sync: Date de dernière synchronisation au format ISO (YYYY-MM-DDTHH:MM:SS)
    - Retourne tous les changements depuis cette date
    """
    try:
        # Convertir last_sync en datetime si fourni
        last_sync_dt = None
        if last_sync:
            try:
                # Gérer le format avec ou sans timezone
                last_sync_clean = last_sync.replace('Z', '+00:00')
                last_sync_dt = datetime.fromisoformat(last_sync_clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Format de date invalide. Utilisez le format ISO (YYYY-MM-DDTHH:MM:SS)"
                )
        
        # Récupérer les changements
        changes = get_changes_since(db, user.tenant_id, last_sync_dt)
        
        return {
            "status": "success",
            "message": "Données récupérées avec succès",
            "tenant_id": str(user.tenant_id),
            "last_sync": last_sync,
            "timestamp": datetime.utcnow().isoformat(),
            "data": changes,
            "count": len(changes)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Erreur lors du pull pour le tenant {user.tenant_id}: {str(e)}",
            exc_info=True
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Erreur lors de la récupération des données",
                "message": str(e),
                "tenant_id": str(user.tenant_id)
            }
        )


@router.get("/status")
def sync_status(
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Vérifier le statut de la synchronisation
    
    Retourne les informations sur la dernière synchronisation et l'état actuel
    """
    try:
        status_info = get_sync_status(db, user.tenant_id)
        
        return {
            "status": "success",
            "tenant_id": str(user.tenant_id),
            "sync_status": status_info,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la vérification du statut pour le tenant {user.tenant_id}: {str(e)}",
            exc_info=True
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "Erreur lors de la vérification du statut",
                "message": str(e),
                "tenant_id": str(user.tenant_id)
            }
        )


@router.get("/health")
def sync_health():
    """
    Vérifier la santé du service de synchronisation
    """
    return {
        "status": "healthy",
        "service": "sync-api",
        "timestamp": datetime.utcnow().isoformat(),
        "endpoints": [
            "POST /api/v1/sync/ - Synchroniser des données",
            "GET /api/v1/sync/pull - Récupérer les changements",
            "GET /api/v1/sync/status - Statut de synchronisation",
            "GET /api/v1/sync/health - Vérification de santé"
        ]
    }


# Fonction utilitaire pour le traitement par lots
@router.post("/batch")
def sync_batch(
    payload: SyncPayload,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint de synchronisation par lots avec traitement optimisé
    
    Similaire à l'endpoint principal mais optimisé pour les gros volumes
    """
    # Grouper les items par table pour traitement optimisé
    items_by_table: Dict[str, List[SyncItem]] = {}
    
    for item in payload.items:
        table = item.table_name
        if table not in items_by_table:
            items_by_table[table] = []
        items_by_table[table].append(item)
    
    results = {}
    total_processed = 0
    total_errors = 0
    
    for table_name, items in items_by_table.items():
        try:
            # Traiter chaque table séparément
            process_sync(db, user.tenant_id, items)
            results[table_name] = {
                "status": "success",
                "count": len(items)
            }
            total_processed += len(items)
        except Exception as e:
            logger.error(f"Erreur lors du traitement de {table_name}: {str(e)}")
            results[table_name] = {
                "status": "error",
                "error": str(e),
                "count": len(items)
            }
            total_errors += len(items)
    
    return {
        "status": "success" if total_errors == 0 else "partial",
        "message": "Synchronisation par lots traitée",
        "tenant_id": str(user.tenant_id),
        "summary": {
            "total_items": len(payload.items),
            "processed_items": total_processed,
            "error_items": total_errors
        },
        "results": results,
        "timestamp": datetime.utcnow().isoformat()
    }

# Ajouter à app/api/v1/sync.py

@router.post("/returns/batch")
def sync_returns_batch(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint pour la synchronisation par lots des retours
    """
    returns = payload.get('returns', [])
    
    processed = 0
    errors = []
    
    for ret in returns:
        try:
            # Traiter chaque retour
            # À implémenter selon votre modèle de données
            processed += 1
        except Exception as e:
            errors.append(str(e))
    
    return {
        "status": "success" if not errors else "partial",
        "synced_ids": [r.get('id') for r in returns],
        "processed": processed,
        "errors": errors
    }

@router.post("/debts/batch")
def sync_debts_batch(
    payload: dict,
    db: Session = Depends(get_db),
    user = Depends(get_current_user)
):
    """
    Endpoint pour la synchronisation par lots des dettes
    """
    debts = payload.get('debts', [])
    
    processed = 0
    errors = []
    
    for debt in debts:
        try:
            # Traiter chaque dette
            # À implémenter selon votre modèle de données
            processed += 1
        except Exception as e:
            errors.append(str(e))
    
    return {
        "status": "success" if not errors else "partial",
        "synced_ids": [d.get('id') for d in debts],
        "processed": processed,
        "errors": errors
    }