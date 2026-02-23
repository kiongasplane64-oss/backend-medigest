# app/api/v1/endpoints/stock.py
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import Optional, List, Dict, Any
from sqlalchemy import DateTime
from sqlalchemy.sql import func
from uuid import UUID
import logging
import datetime
from app.db.session import get_db
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.stock import (
    ProductCreate, ProductUpdate, ProductInDB, ProductResponse,
    ProductListResponse, StockAdjustment, ProductSearch, ExportFormat,
    StockStats, ProductMergeRequest, InventoryCountRequest
)
from app.api.deps import get_current_tenant, get_current_user
from app.services.export import ExportService
from app.core.security import require_permission
from app.services.stock import StockService

router = APIRouter(prefix="/stock", tags=["stock"])
logger = logging.getLogger(__name__)

# Service de gestion des stocks
stock_service = StockService()

# ==============================================
# ROUTES DE BASE POUR LES PRODUITS
# ==============================================

@router.get("/test", summary="Test de l'API Stock")
async def test_stock():
    """Vérifie que l'API Stock fonctionne"""
    return {"message": "Stock API fonctionne !", "version": "1.0"}

@router.get("/", response_model=ProductListResponse, summary="Liste des produits")
@require_permission("view_stock")
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = None,
    category: Optional[str] = None,
    stock_status: Optional[str] = None,
    expiry_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère la liste des produits avec pagination et filtres.
    """
    try:
        # Construire la requête de base
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )
        
        # Appliquer les filtres
        if search:
            query = query.filter(
                (Product.name.ilike(f"%{search}%")) |
                (Product.code.ilike(f"%{search}%")) |
                (Product.barcode.ilike(f"%{search}%")) |
                (Product.commercial_name.ilike(f"%{search}%"))
            )
        
        if category:
            query = query.filter(Product.category == category)
        
        if stock_status:
            query = query.filter(Product.stock_status == stock_status)
        
        if expiry_status:
            query = query.filter(Product.expiry_status == expiry_status)
        
        # Compter le total
        total = query.count()
        
        # Récupérer les produits avec pagination
        products = query.offset(skip).limit(limit).all()
        
        # Calculer les statistiques
        stats = stock_service.calculate_stock_stats(products)
        
        # Convertir en schéma Pydantic
        product_list = [ProductInDB.from_orm(p) for p in products]
        
        return ProductListResponse(
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            limit=limit,
            products=product_list,
            summary=stats
        )
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des produits: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/{product_id}", response_model=ProductInDB, summary="Détails d'un produit")
@require_permission("view_stock")
async def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les détails d'un produit spécifique.
    """
    product = db.query(Product).filter(
        Product.id == product_id,
        Product.tenant_id == current_tenant.id,
        Product.is_active == True
    ).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    
    return ProductInDB.from_orm(product)

@router.post("/", response_model=ProductResponse, summary="Créer un produit")
@require_permission("manage_stock")
async def create_product(
    product_data: ProductCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Crée un nouveau produit dans le stock.
    """
    try:
        # Vérifier si un produit similaire existe déjà
        existing_product = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.name == product_data.name,
            Product.expiry_date == product_data.expiry_date,
            Product.is_active == True
        ).first()
        
        if existing_product:
            # Fusionner les quantités
            new_quantity = existing_product.quantity + product_data.quantity
            existing_product.quantity = new_quantity
            existing_product.available_quantity = new_quantity
            
            # Mettre à jour les prix si nécessaire
            if product_data.purchase_price:
                existing_product.purchase_price = product_data.purchase_price
            if product_data.selling_price:
                existing_product.selling_price = product_data.selling_price
            
            # Mettre à jour les statuts
            existing_product.update_stock_status()
            existing_product.update_expiry_status()
            
            db.commit()
            db.refresh(existing_product)
            
            return ProductResponse(
                message="Produit existant mis à jour - Quantités fusionnées",
                product=ProductInDB.from_orm(existing_product)
            )
        
        # Créer un nouveau produit
        product = Product(
            **product_data.dict(exclude_unset=True),
            tenant_id=current_tenant.id,
            available_quantity=product_data.quantity
        )
        
        # Calculer les prix automatiquement si configuré
        if current_tenant.get_config_value('calcul_auto_prix', True):
            margin = current_tenant.get_config_value('marge_par_defaut', 30.0)
            tva_rate = current_tenant.get_config_value('taux_tva', 0.0) if product_data.has_tva else 0.0
            product.calculate_prices(margin, tva_rate)
        
        # Mettre à jour les statuts
        product.update_stock_status()
        product.update_expiry_status()
        
        db.add(product)
        db.commit()
        db.refresh(product)
        
        logger.info(f"Produit créé: {product.name} par {current_user.email}")
        
        return ProductResponse(
            message="Produit créé avec succès",
            product=ProductInDB.from_orm(product)
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.put("/{product_id}", response_model=ProductResponse, summary="Modifier un produit")
@require_permission("manage_stock")
async def update_product(
    product_id: UUID,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Modifie un produit existant.
    """
    try:
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Mettre à jour les champs
        update_data = product_data.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)
        
        # Recalculer les prix si nécessaire
        if 'purchase_price' in update_data and current_tenant.get_config_value('calcul_auto_prix', True):
            margin = current_tenant.get_config_value('marge_par_defaut', 30.0)
            product.calculate_prices(margin, product.tva_rate if product.has_tva else 0.0)
        
        # Mettre à jour les statuts
        product.update_stock_status()
        product.update_expiry_status()
        
        db.commit()
        db.refresh(product)
        
        logger.info(f"Produit modifié: {product.name} par {current_user.email}")
        
        return ProductResponse(
            message="Produit mis à jour avec succès",
            product=ProductInDB.from_orm(product)
        )
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur modification produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.delete("/{product_id}", summary="Supprimer un produit")
@require_permission("manage_stock")
async def delete_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Supprime un produit (soft delete).
    """
    try:
        product = db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == current_tenant.id
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si le produit a des ventes associées
        if product.total_sold > 0:
            # Soft delete
            product.is_active = False
            product.is_available = False
            message = "Produit désactivé (a des ventes associées)"
        else:
            # Hard delete (ou soft delete selon la stratégie)
            product.is_active = False
            product.is_available = False
            message = "Produit supprimé"
        
        db.commit()
        
        logger.info(f"Produit supprimé/désactivé: {product.name} par {current_user.email}")
        
        return {"message": message, "product_id": str(product_id)}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur suppression produit: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

# ==============================================
# ROUTES POUR LA GESTION DU STOCK
# ==============================================

@router.post("/adjust", summary="Ajuster le stock")
@require_permission("manage_stock")
async def adjust_stock(
    adjustment: StockAdjustment,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Ajuste manuellement la quantité d'un produit.
    """
    try:
        product = db.query(Product).filter(
            Product.id == adjustment.product_id,
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Vérifier si la modification est autorisée
        if current_tenant.get_config_value('lock_stock_modification', False):
            if current_user.role != 'administrateur':
                raise HTTPException(
                    status_code=403,
                    detail="La modification des stocks est verrouillée. Contactez un administrateur."
                )
        
        # Ajuster la quantité
        old_quantity = product.quantity
        product.quantity = adjustment.new_quantity
        product.available_quantity = max(0, adjustment.new_quantity - product.reserved_quantity)
        product.last_adjustment_date = datetime.utcnow()
        
        # Mettre à jour les statuts
        product.update_stock_status()
        
        # Créer un enregistrement d'ajustement (dans une table séparée)
        # À implémenter avec un modèle StockAdjustment
        
        db.commit()
        db.refresh(product)
        
        logger.info(f"Stock ajusté: {product.name} {old_quantity}→{adjustment.new_quantity} par {current_user.email}")
        
        return {
            "message": "Stock ajusté avec succès",
            "product": ProductInDB.from_orm(product),
            "adjustment": {
                "old_quantity": old_quantity,
                "new_quantity": adjustment.new_quantity,
                "difference": adjustment.new_quantity - old_quantity,
                "reason": adjustment.reason,
                "notes": adjustment.notes
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur ajustement stock: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.post("/inventory/count", summary="Comptage d'inventaire")
@require_permission("manage_stock")
async def inventory_count(
    count_request: InventoryCountRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Enregistre un comptage d'inventaire physique.
    """
    try:
        product = db.query(Product).filter(
            Product.id == count_request.product_id,
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).first()
        
        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")
        
        # Enregistrer la différence
        difference = count_request.counted_quantity - product.quantity
        
        # Mettre à jour le produit
        product.quantity = count_request.counted_quantity
        product.available_quantity = max(0, count_request.counted_quantity - product.reserved_quantity)
        product.last_adjustment_date = datetime.utcnow()
        product.update_stock_status()
        
        # Créer un enregistrement d'inventaire
        # À implémenter avec un modèle PhysicalInventory
        
        db.commit()
        
        return {
            "message": "Comptage d'inventaire enregistré",
            "product": ProductInDB.from_orm(product),
            "inventory": {
                "counted_quantity": count_request.counted_quantity,
                "system_quantity": product.quantity - difference,  # ancienne quantité
                "difference": difference,
                "notes": count_request.notes
            }
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur comptage inventaire: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

# ==============================================
# ROUTES POUR L'ANALYSE ET LES STATISTIQUES
# ==============================================

@router.get("/stats/overview", response_model=StockStats, summary="Statistiques globales")
@require_permission("view_stock")
async def get_stock_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les statistiques globales du stock.
    """
    try:
        products = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).all()
        
        stats = stock_service.calculate_detailed_stats(products)
        
        return StockStats(**stats)
        
    except Exception as e:
        logger.error(f"Erreur statistiques stock: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/stats/categories", summary="Statistiques par catégorie")
@require_permission("view_stock")
async def get_category_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les statistiques du stock par catégorie.
    """
    try:
        results = db.query(
            Product.category,
            func.count(Product.id).label('product_count'),
            func.sum(Product.quantity).label('total_quantity'),
            func.sum(Product.quantity * Product.purchase_price).label('total_purchase_value'),
            func.sum(Product.quantity * Product.selling_price).label('total_selling_value')
        ).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.category.isnot(None)
        ).group_by(Product.category).all()
        
        categories = []
        for r in results:
            categories.append({
                "category": r.category,
                "product_count": r.product_count,
                "total_quantity": r.total_quantity or 0,
                "total_purchase_value": float(r.total_purchase_value or 0),
                "total_selling_value": float(r.total_selling_value or 0),
                "total_margin": float((r.total_selling_value or 0) - (r.total_purchase_value or 0))
            })
        
        return {"categories": categories}
        
    except Exception as e:
        logger.error(f"Erreur statistiques catégories: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/alerts/stock", summary="Alertes de stock")
@require_permission("view_stock")
async def get_stock_alerts(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les produits avec des alertes de stock (rupture, critique).
    """
    try:
        # Produits en rupture
        out_of_stock = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.quantity <= 0
        ).all()
        
        # Produits avec stock critique
        low_stock = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.quantity > 0,
            Product.quantity <= Product.alert_threshold
        ).all()
        
        # Produits avec stock élevé
        over_stock = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.maximum_stock.isnot(None),
            Product.quantity > Product.maximum_stock
        ).all()
        
        return {
            "out_of_stock": [ProductInDB.from_orm(p) for p in out_of_stock],
            "low_stock": [ProductInDB.from_orm(p) for p in low_stock],
            "over_stock": [ProductInDB.from_orm(p) for p in over_stock],
            "counts": {
                "out_of_stock": len(out_of_stock),
                "low_stock": len(low_stock),
                "over_stock": len(over_stock)
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur alertes stock: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/alerts/expiry", summary="Alertes de péremption")
@require_permission("view_stock")
async def get_expiry_alerts(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Récupère les produits avec des alertes de péremption.
    """
    try:
        from datetime import date, timedelta
        
        today = date.today()
        warning_date = today + timedelta(days=days)
        
        # Produits expirés
        expired = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date < today
        ).all()
        
        # Produits expirant bientôt
        expiring_soon = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True,
            Product.expiry_date.isnot(None),
            Product.expiry_date >= today,
            Product.expiry_date <= warning_date
        ).all()
        
        return {
            "expired": [ProductInDB.from_orm(p) for p in expired],
            "expiring_soon": [ProductInDB.from_orm(p) for p in expiring_soon],
            "counts": {
                "expired": len(expired),
                "expiring_soon": len(expiring_soon)
            },
            "days_threshold": days
        }
        
    except Exception as e:
        logger.error(f"Erreur alertes péremption: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

# ==============================================
# ROUTES POUR LA FUSION DES PRODUITS
# ==============================================

@router.post("/merge", summary="Fusionner des produits")
@require_permission("manage_stock")
async def merge_products(
    merge_request: ProductMergeRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Fusionne plusieurs produits en un seul.
    """
    try:
        # Récupérer les produits à fusionner
        products = db.query(Product).filter(
            Product.id.in_(merge_request.product_ids),
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).all()
        
        if len(products) < 2:
            raise HTTPException(status_code=400, detail="Au moins 2 produits requis pour la fusion")
        
        # Vérifier que le produit à conserver existe
        keep_product = None
        for p in products:
            if p.id == merge_request.keep_product_id:
                keep_product = p
                break
        
        if not keep_product:
            raise HTTPException(status_code=404, detail="Produit à conserver non trouvé")
        
        # Appliquer la stratégie de fusion
        result = stock_service.merge_products(
            products=products,
            keep_product=keep_product,
            merge_strategy=merge_request.merge_strategy,
            expiry_strategy=merge_request.expiry_strategy
        )
        
        # Désactiver les autres produits
        for p in products:
            if p.id != merge_request.keep_product_id:
                # Vérifier s'il a des ventes
                if p.total_sold > 0:
                    p.is_active = False
                    p.is_available = False
                else:
                    db.delete(p)
        
        db.commit()
        db.refresh(keep_product)
        
        logger.info(f"Produits fusionnés par {current_user.email}: {[str(p.id) for p in products]} → {keep_product.id}")
        
        return {
            "message": "Produits fusionnés avec succès",
            "merged_product": ProductInDB.from_orm(keep_product),
            "merged_details": result
        }
        
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur fusion produits: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur: {str(e)}")

@router.get("/duplicates", summary="Rechercher les doublons")
@require_permission("view_stock")
async def find_duplicates(
    similarity_threshold: float = Query(0.8, ge=0.1, le=1.0),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Recherche les produits potentiellement dupliqués.
    """
    try:
        duplicates = stock_service.find_duplicate_products(
            db=db,
            tenant_id=current_tenant.id,
            similarity_threshold=similarity_threshold
        )
        
        return {
            "duplicates": duplicates,
            "total_groups": len(duplicates),
            "similarity_threshold": similarity_threshold
        }
        
    except Exception as e:
        logger.error(f"Erreur recherche doublons: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

# ==============================================
# ROUTES POUR L'EXPORT/IMPORT
# ==============================================

@router.post("/export", summary="Exporter le stock")
@require_permission("export_data")
async def export_stock(
    export_format: ExportFormat = ExportFormat.EXCEL,
    search: Optional[ProductSearch] = None,
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Exporte le stock dans différents formats (Excel, PDF, CSV).
    """
    try:
        # Construire la requête
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )
        
        # Appliquer les filtres de recherche
        if search:
            if search.query:
                query = query.filter(
                    (Product.name.ilike(f"%{search.query}%")) |
                    (Product.code.ilike(f"%{search.query}%"))
                )
            
            if search.category:
                query = query.filter(Product.category == search.category)
            
            if search.stock_status:
                query = query.filter(Product.stock_status == search.stock_status)
            
            if search.expiry_status:
                query = query.filter(Product.expiry_status == search.expiry_status)
        
        products = query.order_by(Product.name).all()
        
        # Préparer les données d'export
        export_data = []
        for p in products:
            product_dict = {
                "code": p.code or "",
                "nom": p.name,
                "nom_commercial": p.commercial_name or "",
                "categorie": p.category or "",
                "prix_achat": float(p.purchase_price),
                "prix_vente": float(p.selling_price),
                "quantite": p.quantity,
                "quantite_disponible": p.available_quantity,
                "seuil_alerte": p.alert_threshold,
                "stock_minimum": p.minimum_stock,
                "stock_maximum": p.maximum_stock or "",
                "date_peremption": p.expiry_date.isoformat() if p.expiry_date else "",
                "jours_restants": p.days_until_expiry or "",
                "statut_stock": p.stock_status,
                "statut_peremption": p.expiry_status,
                "valeur_achat": float(p.purchase_value),
                "valeur_vente": float(p.selling_value),
                "marge_totale": float(p.total_margin),
                "taux_marge": float(p.margin_rate),
                "fournisseur": p.main_supplier or "",
                "emplacement": p.location or "",
                "code_barres": p.barcode or "",
                "laboratoire": p.laboratory or "",
                "forme_galenique": p.galenic_form or "",
                "dci": p.dci or ""
            }
            export_data.append(product_dict)
        
        if background_tasks:
            # Exécuter en arrière-plan
            export_service = ExportService(current_tenant)
            background_tasks.add_task(
                export_service.export_stock,
                data=export_data,
                export_format=export_format,
                user_id=current_user.id
            )
            return {
                "message": "Export démarré en arrière-plan",
                "format": export_format.value,
                "item_count": len(export_data),
                "user_email": current_user.email,
                "timestamp": datetime.utcnow().isoformat()
            }
        
        # Retourner directement les données (pour les petits exports)
        return {
            "data": export_data,
            "format": export_format.value,
            "count": len(export_data),
            "summary": {
                "total_valeur_achat": sum(d["valeur_achat"] for d in export_data),
                "total_valeur_vente": sum(d["valeur_vente"] for d in export_data),
                "total_marge": sum(d["marge_totale"] for d in export_data),
                "total_produits": len(export_data),
                "total_quantite": sum(d["quantite"] for d in export_data)
            }
        }
        
    except Exception as e:
        logger.error(f"Erreur export stock: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur d'export: {str(e)}")

@router.post("/import/template", summary="Générer un modèle d'importation")
@require_permission("import_data")
async def generate_import_template(
    export_format: ExportFormat = ExportFormat.EXCEL,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Génère un modèle pour l'importation de produits.
    """
    try:
        export_service = ExportService(current_tenant)
        template_data = export_service.generate_import_template()
        
        if export_format == ExportFormat.EXCEL:
            file_path = export_service.export_to_excel(
                data=template_data,
                filename=f"modele_import_produits_{current_tenant.nom_pharmacie}"
            )
            
            # Retourner le fichier ou le chemin
            return {
                "message": "Modèle généré avec succès",
                "file_path": file_path,
                "format": "excel",
                "columns": list(template_data[0].keys()) if template_data else []
            }
        
        return {
            "message": "Modèle généré",
            "data": template_data,
            "format": export_format.value
        }
        
    except Exception as e:
        logger.error(f"Erreur génération modèle: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur: {str(e)}")

@router.post("/import", summary="Importer des produits")
@require_permission("import_data")
async def import_products(
    file_data: dict,  # À adapter pour recevoir un fichier
    import_mode: str = Query("add", regex="^(add|replace|update)$"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Importe des produits à partir d'un fichier.
    """
    try:
        # Implémentation de l'importation
        # À adapter selon le format de fichier reçu
        
        result = {
            "message": "Importation en cours de développement",
            "mode": import_mode,
            "items_processed": 0,
            "success": 0,
            "errors": 0
        }
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur importation: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Erreur d'importation: {str(e)}")

# ==============================================
# ROUTES POUR L'ANALYSE AVANCÉE
# ==============================================

@router.get("/analysis/value", summary="Analyse de la valeur du stock")
@require_permission("view_stock")
async def analyze_stock_value(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Analyse détaillée de la valeur du stock.
    """
    try:
        products = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).all()
        
        analysis = stock_service.analyze_stock_value(products)
        
        return analysis
        
    except Exception as e:
        logger.error(f"Erreur analyse valeur: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/analysis/rotation", summary="Analyse de rotation des stocks")
@require_permission("view_stock")
async def analyze_stock_rotation(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Analyse la rotation des stocks sur une période donnée.
    """
    try:
        # Cette analyse nécessite des données de ventes
        # À implémenter avec les modèles de ventes
        
        return {
            "message": "Analyse de rotation en cours de développement",
            "period_days": days
        }
        
    except Exception as e:
        logger.error(f"Erreur analyse rotation: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/analysis/abc", summary="Analyse ABC des stocks")
@require_permission("view_stock")
async def analyze_abc_stock(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Effectue une analyse ABC des stocks (Pareto).
    """
    try:
        products = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        ).all()
        
        abc_analysis = stock_service.perform_abc_analysis(products)
        
        return abc_analysis
        
    except Exception as e:
        logger.error(f"Erreur analyse ABC: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

# ==============================================
# ROUTES DE RECHERCHE AVANCÉE
# ==============================================

@router.post("/search/advanced", summary="Recherche avancée")
@require_permission("view_stock")
async def advanced_search(
    search: ProductSearch,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Recherche avancée de produits avec multiples critères.
    """
    try:
        query = db.query(Product).filter(
            Product.tenant_id == current_tenant.id,
            Product.is_active == True
        )
        
        # Appliquer tous les filtres
        if search.query:
            query = query.filter(
                (Product.name.ilike(f"%{search.query}%")) |
                (Product.code.ilike(f"%{search.query}%")) |
                (Product.barcode.ilike(f"%{search.query}%")) |
                (Product.commercial_name.ilike(f"%{search.query}%")) |
                (Product.active_ingredient.ilike(f"%{search.query}%")) |
                (Product.dci.ilike(f"%{search.query}%"))
            )
        
        if search.category:
            query = query.filter(Product.category == search.category)
        
        if search.supplier:
            query = query.filter(Product.main_supplier.ilike(f"%{search.supplier}%"))
        
        if search.stock_status:
            query = query.filter(Product.stock_status == search.stock_status)
        
        if search.expiry_status:
            query = query.filter(Product.expiry_status == search.expiry_status)
        
        if search.barcode:
            query = query.filter(Product.barcode == search.barcode)
        
        if search.code:
            query = query.filter(Product.code == search.code)
        
        total = query.count()
        products = query.offset(skip).limit(limit).all()
        
        return ProductListResponse(
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            limit=limit,
            products=[ProductInDB.from_orm(p) for p in products],
            summary=stock_service.calculate_stock_stats(products)
        )
        
    except Exception as e:
        logger.error(f"Erreur recherche avancée: {str(e)}")
        raise HTTPException(status_code=500, detail="Erreur interne du serveur")

@router.get("/barcode/{barcode}", summary="Recherche par code-barres")
@require_permission("view_stock")
async def search_by_barcode(
    barcode: str,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user)
):
    """
    Recherche un produit par son code-barres.
    """
    product = db.query(Product).filter(
        Product.barcode == barcode,
        Product.tenant_id == current_tenant.id,
        Product.is_active == True
    ).first()
    
    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")
    
    return ProductInDB.from_orm(product)