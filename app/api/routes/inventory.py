# app/api/routes/inventory.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4
from datetime import datetime, date, timedelta
import logging
from pathlib import Path
from app.db.session import get_db
from app.models.inventory import PhysicalInventory, InventoryItem, InventorySchedule
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.inventory import (
    InventoryCreate, InventoryInDB, InventoryUpdate,
    InventoryItemCreate, InventoryItemInDB, InventoryItemUpdate,
    InventoryReport, ScheduleCreate
)
from app.api.deps import get_current_tenant, get_current_user
from app.core.security import require_permission
from app.services.inventory import InventoryService

router = APIRouter(prefix="/inventory", tags=["Inventory"])
logger = logging.getLogger(__name__)


@router.post("/", response_model=InventoryInDB)
@require_permission("inventory_manage")
def create_inventory(
    inventory_data: InventoryCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouvel inventaire physique
    """
    try:
        # Générer le numéro d'inventaire
        inventory_number = f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid4().hex[:8].upper()}"
        
        # Créer l'inventaire
        inventory = PhysicalInventory(
            tenant_id=current_tenant.id,
            inventory_number=inventory_number,
            inventory_type=inventory_data.inventory_type.value,
            description=inventory_data.description,
            planned_date=inventory_data.planned_date,
            tags=inventory_data.tags,
            created_by=current_user.id,
            status="draft"
        )
        
        db.add(inventory)
        db.flush()  # Pour obtenir l'ID
        
        # Ajouter les items selon le type d'inventaire
        if inventory_data.inventory_type == "partial" and inventory_data.product_ids:
            # Inventaire partiel - seulement les produits spécifiés
            products = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.id.in_(inventory_data.product_ids)
            ).all()
        else:
            # Inventaire complet - tous les produits actifs
            products = db.query(Product).filter(
                Product.tenant_id == current_tenant.id,
                Product.is_active == True
            ).all()
        
        # Créer les items d'inventaire - CORRECTION : déplacer la création hors de la boucle
        inventory_items = [
            InventoryItem(
                tenant_id=current_tenant.id,
                inventory_id=inventory.id,
                product_id=product.id,
                expected_quantity=product.quantity,
                expected_value=product.quantity * product.purchase_price if product.purchase_price else 0,
                status="pending"
            ) for product in products
        ]

        if inventory_items:
            db.bulk_save_objects(inventory_items)
        
        db.commit()
        
        logger.info(f"Inventaire créé: {inventory_number} par {current_user.full_name}")
        
        # Recharger l'inventaire pour avoir les relations
        db.refresh(inventory)
        return inventory
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création de l'inventaire: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création de l'inventaire: {str(e)}"
        )


@router.get("/", response_model=List[InventoryInDB])
@require_permission("inventory_view")
def list_inventories(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = None,
    inventory_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None
):
    """
    Liste les inventaires avec filtres
    """
    query = db.query(PhysicalInventory).filter(
        PhysicalInventory.tenant_id == current_tenant.id
    )
    
    # Appliquer les filtres
    if status:
        query = query.filter(PhysicalInventory.status == status)
    
    if inventory_type:
        query = query.filter(PhysicalInventory.inventory_type == inventory_type)
    
    if start_date:
        query = query.filter(PhysicalInventory.created_at >= start_date)
    
    if end_date:
        query = query.filter(PhysicalInventory.created_at <= end_date)
    
    # Trier par date de création décroissante
    inventories = query.order_by(
        PhysicalInventory.created_at.desc()
    ).offset(skip).limit(limit).all()
    
    return inventories


@router.get("/{inventory_id}", response_model=InventoryReport)
@require_permission("inventory_view")
def get_inventory(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère un inventaire avec ses items
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    # Récupérer les items avec les informations des produits
    items_query = db.query(
        InventoryItem,
        Product.name.label('product_name'),
        Product.code.label('product_code')
    ).join(
        Product, InventoryItem.product_id == Product.id
    ).filter(
        InventoryItem.inventory_id == inventory_id,
        InventoryItem.tenant_id == current_tenant.id
    ).all()
    
    # Formater les items
    inventory_items = []
    for item, product_name, product_code in items_query:
        # CORRECTION : conversion explicite en dictionnaire puis création du schéma
        item_dict = {
            "id": item.id,
            "tenant_id": item.tenant_id,
            "inventory_id": item.inventory_id,
            "product_id": item.product_id,
            "expected_quantity": item.expected_quantity,
            "counted_quantity": item.counted_quantity,
            "variance": item.variance,
            "variance_percentage": item.variance_percentage,
            "batch_number": item.batch_number,
            "expiry_date": item.expiry_date,
            "location": item.location,
            "notes": item.notes,
            "status": item.status,
            "counted_at": item.counted_at,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "product_name": product_name,
            "product_code": product_code
        }
        inventory_items.append(InventoryItemInDB(**item_dict))
    
    # Calculer le résumé
    summary = {
        "total_items": inventory.total_items or 0,
        "items_counted": inventory.items_counted or 0,
        "items_missing": inventory.items_missing or 0,
        "items_excess": inventory.items_excess or 0,
        "completion_rate": (inventory.items_counted / inventory.total_items * 100) if inventory.total_items and inventory.total_items > 0 else 0,
        "system_value": float(inventory.system_value) if inventory.system_value else 0,
        "counted_value": float(inventory.counted_value) if inventory.counted_value else 0,
        "variance_value": float(inventory.variance_value) if inventory.variance_value else 0,
        "variance_percentage": float(inventory.variance_percentage) if inventory.variance_percentage else 0
    }
    
    # Générer des recommandations
    recommendations = []
    if summary["variance_percentage"] > 5:
        recommendations.append("Écart significatif détecté. Vérifier les procédures de stockage.")
    if summary["items_missing"] > 0:
        recommendations.append(f"{summary['items_missing']} items manquants. Investigation requise.")
    if summary["items_counted"] < summary["total_items"]:
        recommendations.append(f"Inventaire incomplet: {summary['total_items'] - summary['items_counted']} items restants.")
    
    return InventoryReport(
        inventory=inventory,
        items=inventory_items,
        summary=summary,
        recommendations=recommendations
    )


@router.post("/{inventory_id}/items")
@require_permission("inventory_manage")
def add_inventory_item(
    inventory_id: UUID,
    item_data: InventoryItemCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Ajoute un item à un inventaire
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    if inventory.status not in ["draft", "in_progress"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inventaire non modifiable"
        )
    
    product = db.query(Product).filter(
        Product.id == item_data.product_id,
        Product.tenant_id == current_tenant.id
    ).first()
    
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Produit non trouvé"
        )
    
    try:
        # Vérifier si l'item existe déjà
        existing_item = db.query(InventoryItem).filter(
            InventoryItem.inventory_id == inventory_id,
            InventoryItem.product_id == item_data.product_id,
            InventoryItem.tenant_id == current_tenant.id
        ).first()
        
        if existing_item:
            # Mettre à jour l'item existant
            existing_item.counted_quantity = item_data.counted_quantity
            existing_item.counted_at = datetime.utcnow()
            existing_item.batch_number = item_data.batch_number
            existing_item.expiry_date = item_data.expiry_date
            existing_item.location = item_data.location
            existing_item.notes = item_data.notes
            existing_item.status = "counted"
            
            # Calculer les variances
            existing_item.calculate_variance()
        else:
            # Créer un nouvel item
            item = InventoryItem(
                tenant_id=current_tenant.id,
                inventory_id=inventory_id,
                product_id=item_data.product_id,
                expected_quantity=product.quantity,
                counted_quantity=item_data.counted_quantity,
                batch_number=item_data.batch_number,
                expiry_date=item_data.expiry_date,
                location=item_data.location,
                notes=item_data.notes,
                counted_at=datetime.utcnow(),
                status="counted"
            )
            item.calculate_variance()
            db.add(item)
        
        # Mettre à jour les statistiques de l'inventaire
        inventory.calculate_variance()
        
        db.commit()
        
        logger.info(f"Item ajouté à l'inventaire {inventory.inventory_number}")
        
        return {"message": "Item ajouté avec succès"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de l'ajout de l'item: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de l'ajout de l'item: {str(e)}"
        )


@router.post("/{inventory_id}/start")
@require_permission("inventory_manage")
def start_inventory(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Démarre un inventaire
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    if inventory.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'inventaire a déjà été démarré"
        )
    
    try:
        inventory.status = "in_progress"
        inventory.start_date = datetime.utcnow()
        db.commit()
        
        logger.info(f"Inventaire démarré: {inventory.inventory_number}")
        
        return {"message": "Inventaire démarré avec succès"}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors du démarrage de l'inventaire: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors du démarrage de l'inventaire: {str(e)}"
        )


@router.post("/{inventory_id}/complete")
@require_permission("inventory_manage")
def complete_inventory(
    inventory_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Termine un inventaire et ajuste les stocks
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    if inventory.status not in ["in_progress", "counting"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'inventaire n'est pas en cours"
        )
    
    try:
        # Marquer l'inventaire comme terminé
        inventory.status = "completed"
        inventory.end_date = datetime.utcnow()
        inventory.validated_by = current_user.id
        
        # Recalculer les variances avant de finaliser
        inventory.calculate_variance()
        
        # Ajuster les stocks pour les items avec variances
        from app.models.stock_movement import StockMovement
        
        for item in inventory.items:
            if item.variance != 0 and item.product:
                # Créer un mouvement de stock pour l'ajustement
                movement = StockMovement(
                    tenant_id=current_tenant.id,
                    product_id=item.product.id,
                    quantity_before=item.expected_quantity,
                    quantity_after=item.counted_quantity or 0,
                    quantity_change=item.variance,
                    movement_type="inventory_adjustment",
                    reason=f"Ajustement d'inventaire {inventory.inventory_number}",
                    reference_number=inventory.inventory_number,
                    created_by=current_user.id
                )
                
                # Mettre à jour le stock du produit
                item.product.quantity = item.counted_quantity or 0
                
                db.add(movement)
        
        db.commit()
        
        # Lancer la génération du rapport en arrière-plan
        background_tasks.add_task(
            generate_inventory_report,
            inventory_id=inventory_id,
            tenant_id=current_tenant.id
        )
        
        logger.info(f"Inventaire terminé: {inventory.inventory_number}")
        
        return {
            "message": "Inventaire terminé avec succès",
            "variance_value": float(inventory.variance_value) if inventory.variance_value else 0,
            "variance_percentage": float(inventory.variance_percentage) if inventory.variance_percentage else 0
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la finalisation de l'inventaire: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la finalisation de l'inventaire: {str(e)}"
        )


@router.get("/{inventory_id}/export")
@require_permission("inventory_view")
def export_inventory(
    inventory_id: UUID,
    export_format: str = Query("excel", regex="^(excel|pdf|csv)$"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Exporte un inventaire dans différents formats
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    if background_tasks:
        # Lancer l'export en arrière-plan
        from app.services.export import ExportService
        export_service = ExportService(current_tenant)
        
        background_tasks.add_task(
            export_service.export_inventory,
            inventory_id=inventory_id,
            export_format=export_format,
            user_id=current_user.id
        )
        
        return {
            "message": "Export démarré en arrière-plan",
            "format": export_format,
            "inventory_number": inventory.inventory_number
        }
    
    # Retour direct pour petits exports
    return {"message": "Export synchrone non implémenté"}


@router.post("/schedules")
@require_permission("inventory_manage")
def create_inventory_schedule(
    schedule_data: ScheduleCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un planning d'inventaire récurrent
    """
    try:
        # Calculer la prochaine date
        today = date.today()
        next_schedule = today
        
        if schedule_data.schedule_type == "daily":
            next_schedule = today + timedelta(days=schedule_data.frequency)
        elif schedule_data.schedule_type == "weekly":
            if schedule_data.day_of_week is not None:
                days_ahead = schedule_data.day_of_week - today.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                next_schedule = today + timedelta(days=days_ahead)
        elif schedule_data.schedule_type == "monthly":
            if schedule_data.day_of_month is not None:
                year = today.year
                month = today.month
                
                if today.day >= schedule_data.day_of_month:
                    month += 1
                    if month > 12:
                        month = 1
                        year += 1
                
                next_schedule = date(year, month, min(schedule_data.day_of_month, 28))
        elif schedule_data.schedule_type == "yearly":
            if schedule_data.month_of_year is not None:
                year = today.year
                if today.month > schedule_data.month_of_year or (
                    today.month == schedule_data.month_of_year and today.day >= 1
                ):
                    year += 1
                next_schedule = date(year, schedule_data.month_of_year, 1)
        
        schedule = InventorySchedule(
            tenant_id=current_tenant.id,
            schedule_type=schedule_data.schedule_type.value,
            frequency=schedule_data.frequency,
            day_of_week=schedule_data.day_of_week,
            day_of_month=schedule_data.day_of_month,
            month_of_year=schedule_data.month_of_year,
            cycle_count=schedule_data.cycle_count or 0,
            description=schedule_data.description,
            next_schedule=next_schedule
        )
        
        db.add(schedule)
        db.commit()
        
        logger.info(f"Planning d'inventaire créé par {current_user.full_name}")
        
        return {"message": "Planning créé avec succès", "next_schedule": next_schedule.isoformat()}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur lors de la création du planning: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur lors de la création du planning: {str(e)}"
        )


async def generate_inventory_report(
    inventory_id: UUID,
    tenant_id: UUID
):
    """
    Génère un rapport d'inventaire en arrière-plan
    """
    db = None
    try:
        from app.db.session import SessionLocal
        db = SessionLocal()
        
        # Récupérer l'inventaire avec ses items
        inventory = db.query(PhysicalInventory).filter(
            PhysicalInventory.id == inventory_id,
            PhysicalInventory.tenant_id == tenant_id
        ).first()
        
        if not inventory:
            logger.error(f"Inventaire {inventory_id} non trouvé")
            return
        
        # Récupérer les items avec les produits
        items_with_products = db.query(
            InventoryItem,
            Product
        ).join(
            Product, InventoryItem.product_id == Product.id
        ).filter(
            InventoryItem.inventory_id == inventory_id,
            InventoryItem.tenant_id == tenant_id
        ).all()
        
        # Préparer les données du rapport
        report_data = {
            "inventory_id": str(inventory.id),
            "inventory_number": inventory.inventory_number,
            "inventory_type": inventory.inventory_type,
            "status": inventory.status,
            "created_at": inventory.created_at.isoformat() if inventory.created_at else None,
            "start_date": inventory.start_date.isoformat() if inventory.start_date else None,
            "end_date": inventory.end_date.isoformat() if inventory.end_date else None,
            "total_items": inventory.total_items or 0,
            "items_counted": inventory.items_counted or 0,
            "items_missing": inventory.items_missing or 0,
            "items_excess": inventory.items_excess or 0,
            "completion_rate": (inventory.items_counted / inventory.total_items * 100) if inventory.total_items and inventory.total_items > 0 else 0,
            "system_value": float(inventory.system_value) if inventory.system_value else 0,
            "counted_value": float(inventory.counted_value) if inventory.counted_value else 0,
            "variance_value": float(inventory.variance_value) if inventory.variance_value else 0,
            "variance_percentage": float(inventory.variance_percentage) if inventory.variance_percentage else 0,
            "items": []
        }
        
        # Ajouter les items
        for item, product in items_with_products:
            item_data = {
                "product_id": str(product.id),
                "product_code": product.code,
                "product_name": product.name,
                "expected_quantity": float(item.expected_quantity) if item.expected_quantity else 0,
                "counted_quantity": float(item.counted_quantity) if item.counted_quantity else 0,
                "variance": float(item.variance) if item.variance else 0,
                "variance_percentage": float(item.variance_percentage) if item.variance_percentage else 0,
                "batch_number": item.batch_number,
                "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                "location": item.location,
                "notes": item.notes,
                "status": item.status,
                "counted_at": item.counted_at.isoformat() if item.counted_at else None
            }
            report_data["items"].append(item_data)
        
        # Générer le rapport
        import json
        from pathlib import Path
        
        # Créer le dossier de rapports
        reports_dir = Path("reports/inventory")
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Nom du fichier
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"inventory_report_{inventory.inventory_number}_{timestamp}.json"
        filepath = reports_dir / filename
        
        # Sauvegarder le rapport JSON
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        # Mettre à jour l'inventaire avec le chemin du rapport
        inventory.report_path = str(filepath)
        db.commit()
        
        logger.info(f"Rapport d'inventaire généré: {filepath}")
        
        return report_data
        
    except Exception as e:
        logger.error(f"Erreur génération rapport inventaire {inventory_id}: {str(e)}")
    finally:
        if db:
            db.close()


@router.get("/{inventory_id}/report")
@require_permission("inventory_view")
async def download_inventory_report(
    inventory_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Télécharge le rapport d'inventaire
    """
    inventory = db.query(PhysicalInventory).filter(
        PhysicalInventory.id == inventory_id,
        PhysicalInventory.tenant_id == current_tenant.id
    ).first()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Inventaire non trouvé"
        )
    
    if not inventory.report_path:
        # Générer le rapport si nécessaire
        report_data = await generate_inventory_report(inventory_id, current_tenant.id)
        
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content=report_data,
            headers={"Content-Disposition": f"attachment; filename=inventory_report_{inventory.inventory_number}.json"}
        )
    
    # Retourner le fichier existant
    import os
    from fastapi.responses import FileResponse
    
    if os.path.exists(inventory.report_path):
        filename = f"inventory_report_{inventory.inventory_number}.json"
        return FileResponse(
            path=inventory.report_path,
            filename=filename,
            media_type="application/json"
        )
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Rapport non disponible"
    )


@router.get("/stats/summary")
@require_permission("inventory_view")
def get_inventory_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None)
):
    """
    Statistiques des inventaires
    """
    query = db.query(PhysicalInventory).filter(
        PhysicalInventory.tenant_id == current_tenant.id,
        PhysicalInventory.status == "completed"
    )
    
    if start_date:
        query = query.filter(PhysicalInventory.end_date >= start_date)
    
    if end_date:
        query = query.filter(PhysicalInventory.end_date <= end_date)
    
    inventories = query.all()
    
    if not inventories:
        return {
            "total_inventories": 0,
            "total_items": 0,
            "average_variance": 0,
            "total_variance_value": 0,
            "inventories_by_type": {},
            "recent_inventories": []
        }
    
    total_variance = sum(float(inv.variance_percentage) for inv in inventories if inv.variance_percentage)
    
    stats = {
        "total_inventories": len(inventories),
        "total_items": sum(inv.total_items or 0 for inv in inventories),
        "total_variance_value": float(sum(float(inv.variance_value) for inv in inventories if inv.variance_value)),
        "average_variance": float(total_variance / len(inventories)) if inventories else 0,
        "inventories_by_type": {},
        "recent_inventories": []
    }
    
    # Distribution par type
    for inv in inventories:
        inv_type = inv.inventory_type or "unknown"
        stats["inventories_by_type"][inv_type] = stats["inventories_by_type"].get(inv_type, 0) + 1
    
    # 5 derniers inventaires
    recent_inventories = sorted(inventories, key=lambda x: x.end_date or x.created_at, reverse=True)[:5]
    stats["recent_inventories"] = [
        {
            "id": str(inv.id),
            "number": inv.inventory_number,
            "type": inv.inventory_type,
            "end_date": inv.end_date.isoformat() if inv.end_date else None,
            "variance_percentage": float(inv.variance_percentage) if inv.variance_percentage else 0,
            "total_items": inv.total_items or 0
        }
        for inv in recent_inventories
    ]
    
    return stats


@router.get("/alerts", response_model=Dict[str, Any])
@require_permission("inventory_view")
def get_inventory_alerts(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Récupère les alertes d'inventaire : stocks bas et produits proches de la péremption
    """
    # Vérifier que les colonnes existent dans le modèle Product
    # Si min_stock n'existe pas, utiliser une valeur par défaut
    from sqlalchemy import inspect
    
    inspector = inspect(Product)
    columns = [c.key for c in inspector.columns]
    
    # 1. Alertes de stock bas
    low_stock_query = db.query(Product).filter(
        Product.tenant_id == current_tenant.id,
        Product.is_active == True
    )
    
    if 'min_stock' in columns:
        low_stock_query = low_stock_query.filter(Product.quantity <= Product.min_stock)
    else:
        # Si min_stock n'existe pas, on considère stock bas si quantité < 10
        low_stock_query = low_stock_query.filter(Product.quantity <= 10)
    
    low_stock = low_stock_query.all()

    # 2. Alertes de péremption (périme dans les 30 prochains jours)
    if 'expiry_date' in columns:
        expiry_threshold = date.today() + timedelta(days=30)
        expiring_soon = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.expiry_date <= expiry_threshold,
            Product.expiry_date >= date.today()
        ).all()

        # 3. Produits déjà périmés
        expired = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.expiry_date < date.today()
        ).all()
    else:
        expiring_soon = []
        expired = []

    return {
        "low_stock_count": len(low_stock),
        "expiring_soon_count": len(expiring_soon),
        "expired_count": len(expired),
        "alerts": {
            "low_stock": [
                {
                    "id": str(p.id), 
                    "name": p.name, 
                    "qty": p.quantity,
                    "min": getattr(p, 'min_stock', 10) if hasattr(p, 'min_stock') else 10
                } 
                for p in low_stock
            ],
            "expiring_soon": [
                {
                    "id": str(p.id), 
                    "name": p.name, 
                    "expiry": p.expiry_date.isoformat() if p.expiry_date else None
                } 
                for p in expiring_soon
            ],
            "expired": [
                {
                    "id": str(p.id), 
                    "name": p.name, 
                    "expiry": p.expiry_date.isoformat() if p.expiry_date else None
                } 
                for p in expired
            ]
        }
    }