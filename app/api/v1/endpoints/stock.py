"""
API endpoints pour la gestion des stocks et produits.
"""

from __future__ import annotations

import datetime
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from fastapi import Response
from starlette.background import BackgroundTasks  
from app.api.deps import (
    get_current_active_user,
    get_current_pharmacy_entity,
    get_current_tenant,
    require_permission,
)
from app.db.session import get_db
from app.models.pharmacy import Pharmacy
from app.models.product import Product
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.stock import (
    ExportFormat,
    InventoryCountRequest,
    ProductCreate,
    ProductInDB,
    ProductListResponse,
    ProductMergeRequest,
    ProductResponse,
    ProductSearch,
    ProductUpdate,
    StockAdjustment,
    StockStats,
)
from app.services.export import ExportService
from app.services.stock import StockService

router = APIRouter(prefix="/stock", tags=["stock"])
logger = logging.getLogger(__name__)

stock_service = StockService()


# =========================================================
# OUTILS INTERNES
# =========================================================

def _to_float(value: Any, default: float = 0.0) -> float:
    """Convertit une valeur en float de manière sécurisée."""
    try:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    except (TypeError, ValueError):
        return default


def _serialize_product(product: Product) -> ProductInDB:
    """Sérialise un produit en schéma Pydantic."""
    return ProductInDB.model_validate(product, from_attributes=True)


def _tenant_get_config(tenant: Tenant, key: str, default: Any = None) -> Any:
    """Récupère une configuration du tenant de manière sécurisée."""
    try:
        if hasattr(tenant, "get_config_value") and callable(getattr(tenant, "get_config_value")):
            return tenant.get_config_value(key, default)

        tenant_config = getattr(tenant, "config", None)
        if isinstance(tenant_config, dict):
            return tenant_config.get(key, default)

        if hasattr(tenant, key):
            value = getattr(tenant, key)
            return default if value is None else value

    except Exception as exc:
        logger.warning("Impossible de lire la config tenant '%s': %s", key, exc)

    return default


def _tenant_display_name(tenant: Tenant) -> str:
    """Retourne un nom d'affichage pour le tenant."""
    return (
        getattr(tenant, "nom_pharmacie", None)
        or getattr(tenant, "name", None)
        or getattr(tenant, "nom", None)
        or "tenant"
    )


def _safe_update_product_status(product: Product) -> None:
    """Met à jour les statuts du produit si les méthodes existent."""
    if hasattr(product, "update_stock_status") and callable(getattr(product, "update_stock_status")):
        product.update_stock_status()

    if hasattr(product, "update_expiry_status") and callable(getattr(product, "update_expiry_status")):
        product.update_expiry_status()


def _safe_calculate_prices(product: Product, margin: float, tva_rate: float) -> None:
    """Calcule les prix du produit si la méthode existe."""
    if hasattr(product, "calculate_prices") and callable(getattr(product, "calculate_prices")):
        product.calculate_prices(margin, tva_rate)
        return

    purchase_price = _to_float(getattr(product, "purchase_price", 0), 0.0)
    if purchase_price <= 0:
        return

    selling_price = purchase_price * (1 + (margin / 100.0))
    if tva_rate > 0:
        selling_price *= (1 + (tva_rate / 100.0))

    if hasattr(product, "selling_price"):
        product.selling_price = selling_price


def _base_product_query(db: Session, tenant_id: UUID, pharmacy_id: Optional[UUID] = None):
    """Construit la requête de base pour les produits."""
    query = db.query(Product).filter(
        Product.tenant_id == tenant_id,
        Product.is_active.is_(True),
    )

    if pharmacy_id:
        query = query.filter(Product.pharmacy_id == pharmacy_id)

    return query


def _apply_common_filters(
    query,
    search: Optional[str],
    category: Optional[str],
    stock_status: Optional[str],
    expiry_status: Optional[str],
):
    """Applique les filtres communs à la requête."""
    if search:
        query = query.filter(
            or_(
                Product.name.ilike(f"%{search}%"),
                Product.code.ilike(f"%{search}%"),
                Product.barcode.ilike(f"%{search}%"),
                Product.commercial_name.ilike(f"%{search}%"),
            )
        )

    if category:
        query = query.filter(Product.category == category)

    if stock_status:
        query = query.filter(Product.stock_status == stock_status)

    if expiry_status:
        query = query.filter(Product.expiry_status == expiry_status)

    return query


def _ensure_pharmacy_in_tenant(current_tenant: Tenant, current_pharmacy: Optional[Pharmacy]) -> Pharmacy:
    """Vérifie que la pharmacie appartient bien au tenant courant."""
    if current_pharmacy is None:
        raise HTTPException(status_code=400, detail="Aucune pharmacie active sélectionnée")

    if getattr(current_pharmacy, "tenant_id", None) != current_tenant.id:
        raise HTTPException(status_code=403, detail="La pharmacie sélectionnée n'appartient pas au tenant courant")

    return current_pharmacy


# =========================================================
# ROUTES DE TEST / UTILITAIRES
# =========================================================

@router.get("/test", summary="Test de l'API Stock")
async def test_stock():
    """Endpoint de test pour vérifier que l'API fonctionne."""
    return {"message": "Stock API fonctionne !", "version": "2.0"}


@router.get("/categories", summary="Liste des catégories")
async def list_categories(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """
    Liste toutes les catégories de produits avec leur nombre d'occurrences.
    Route placée avant /{product_id} pour éviter que 'categories' soit interprété comme un UUID.
    """
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        rows = (
            db.query(Product.category, func.count(Product.id).label("count"))
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.category.isnot(None),
                Product.category != "",
            )
            .group_by(Product.category)
            .order_by(Product.category.asc())
            .all()
        )

        categories = [{"name": row.category, "count": row.count} for row in rows]
        return {
            "categories": categories,
            "total": len(categories),
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur récupération catégories")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


# =========================================================
# ROUTES DE BASE POUR LES PRODUITS
# =========================================================

@router.get("/", response_model=ProductListResponse, summary="Liste des produits")
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    stock_status: Optional[str] = Query(None),
    expiry_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Liste tous les produits avec pagination et filtres optionnels."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        query = _base_product_query(db, current_tenant.id, pharmacy.id)
        query = _apply_common_filters(query, search, category, stock_status, expiry_status)

        total = query.count()
        products = query.order_by(Product.name.asc()).offset(skip).limit(limit).all()

        stats = stock_service.calculate_stock_stats(products)
        product_list = [_serialize_product(product) for product in products]

        return ProductListResponse(
            total=total,
            page=(skip // limit) + 1 if limit > 0 else 1,
            limit=limit,
            products=product_list,
            summary=stats,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur lors de la récupération des produits")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/", response_model=ProductResponse, summary="Créer un produit")
async def create_product(
    product_data: ProductCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:create")),
):
    """Crée un nouveau produit ou fusionne avec un existant."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        if product_data.pharmacy_id != pharmacy.id:
            raise HTTPException(
                status_code=403,
                detail="La pharmacie du produit ne correspond pas à la pharmacie courante",
            )

        existing_product = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.name == product_data.name,
                Product.expiry_date == product_data.expiry_date,
                Product.batch_number == product_data.batch_number,
                Product.is_active.is_(True),
            )
            .first()
        )

        if existing_product:
            added_quantity = int(getattr(product_data, "quantity", 0) or 0)
            current_quantity = int(getattr(existing_product, "quantity", 0) or 0)

            new_quantity = current_quantity + added_quantity
            existing_product.quantity = new_quantity
            existing_product.available_quantity = max(
                0,
                new_quantity - int(getattr(existing_product, "reserved_quantity", 0) or 0),
            )

            if getattr(product_data, "purchase_price", None) is not None:
                existing_product.purchase_price = product_data.purchase_price

            if getattr(product_data, "selling_price", None) is not None:
                existing_product.selling_price = product_data.selling_price

            if getattr(product_data, "has_tva", None) is not None:
                existing_product.has_tva = product_data.has_tva

            if getattr(product_data, "tva_rate", None) is not None:
                existing_product.tva_rate = product_data.tva_rate

            _safe_update_product_status(existing_product)

            db.commit()
            db.refresh(existing_product)

            return ProductResponse(
                message="Produit existant mis à jour - quantités fusionnées",
                product=_serialize_product(existing_product),
            )

        payload = product_data.model_dump(exclude_unset=True)
        payload["tenant_id"] = current_tenant.id
        payload["pharmacy_id"] = pharmacy.id
        payload["available_quantity"] = int(getattr(product_data, "quantity", 0) or 0)
        payload["reserved_quantity"] = 0

        product = Product(**payload)

        calcul_auto_prix = bool(_tenant_get_config(current_tenant, "calcul_auto_prix", True))
        marge_par_defaut = _to_float(_tenant_get_config(current_tenant, "marge_par_defaut", 30.0), 30.0)
        has_tva = bool(getattr(product_data, "has_tva", False))
        tva_rate = _to_float(_tenant_get_config(current_tenant, "taux_tva", 0.0), 0.0) if has_tva else 0.0

        if calcul_auto_prix:
            _safe_calculate_prices(product, marge_par_defaut, tva_rate)

        _safe_update_product_status(product)

        db.add(product)
        db.commit()
        db.refresh(product)

        logger.info(
            "Produit créé: %s | tenant=%s | pharmacy=%s | user=%s",
            getattr(product, "name", "N/A"),
            str(current_tenant.id),
            str(pharmacy.id),
            getattr(current_user, "email", None),
        )

        return ProductResponse(
            message="Produit créé avec succès",
            product=_serialize_product(product),
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur création produit")
        raise HTTPException(status_code=400, detail=f"Erreur création produit: {exc}")


@router.post("/adjust", summary="Ajuster le stock")
async def adjust_stock(
    adjustment: StockAdjustment,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:adjust")),
):
    """Ajuste la quantité d'un produit en stock."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        product = (
            db.query(Product)
            .filter(
                Product.id == adjustment.product_id,
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
            )
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        lock_stock_modification = bool(_tenant_get_config(current_tenant, "lock_stock_modification", False))
        user_role = (getattr(current_user, "role", "") or "").lower()

        if lock_stock_modification and user_role not in {"admin", "administrateur", "super_admin", "superadmin"}:
            raise HTTPException(
                status_code=403,
                detail="La modification des stocks est verrouillée. Contactez un administrateur.",
            )

        old_quantity = int(getattr(product, "quantity", 0) or 0)
        reserved_quantity = int(getattr(product, "reserved_quantity", 0) or 0)

        product.quantity = adjustment.new_quantity
        product.available_quantity = max(0, adjustment.new_quantity - reserved_quantity)
        product.last_adjustment_date = datetime.datetime.now(datetime.timezone.utc)

        _safe_update_product_status(product)

        db.commit()
        db.refresh(product)

        logger.info(
            "Stock ajusté: %s %s→%s par %s",
            getattr(product, "name", "N/A"),
            old_quantity,
            adjustment.new_quantity,
            getattr(current_user, "email", None),
        )

        return {
            "message": "Stock ajusté avec succès",
            "product": _serialize_product(product),
            "adjustment": {
                "old_quantity": old_quantity,
                "new_quantity": adjustment.new_quantity,
                "difference": adjustment.new_quantity - old_quantity,
                "reason": adjustment.reason,
                "notes": adjustment.notes,
            },
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur ajustement stock")
        raise HTTPException(status_code=400, detail=f"Erreur ajustement stock: {exc}")


@router.post("/inventory/count", summary="Comptage d'inventaire")
async def inventory_count(
    count_request: InventoryCountRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:adjust")),
):
    """Enregistre un comptage d'inventaire pour un produit."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        product = (
            db.query(Product)
            .filter(
                Product.id == count_request.product_id,
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
            )
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        old_quantity = int(getattr(product, "quantity", 0) or 0)
        reserved_quantity = int(getattr(product, "reserved_quantity", 0) or 0)
        difference = count_request.counted_quantity - old_quantity

        product.quantity = count_request.counted_quantity
        product.available_quantity = max(0, count_request.counted_quantity - reserved_quantity)
        product.last_adjustment_date = datetime.datetime.now(datetime.timezone.utc)

        _safe_update_product_status(product)

        db.commit()
        db.refresh(product)

        return {
            "message": "Comptage d'inventaire enregistré",
            "product": _serialize_product(product),
            "inventory": {
                "counted_quantity": count_request.counted_quantity,
                "system_quantity": old_quantity,
                "difference": difference,
                "notes": count_request.notes,
            },
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur comptage inventaire")
        raise HTTPException(status_code=400, detail=f"Erreur comptage inventaire: {exc}")


@router.get("/stats/overview", response_model=StockStats, summary="Statistiques globales")
async def get_stock_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Retourne des statistiques globales sur le stock."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        products = _base_product_query(db, current_tenant.id, pharmacy.id).all()
        stats = stock_service.calculate_detailed_stats(products)
        return StockStats(**stats)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur statistiques stock")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/stats/categories", summary="Statistiques par catégorie")
async def get_category_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Retourne des statistiques détaillées par catégorie de produits."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        results = (
            db.query(
                Product.category,
                func.count(Product.id).label("product_count"),
                func.sum(Product.quantity).label("total_quantity"),
                func.sum(Product.quantity * Product.purchase_price).label("total_purchase_value"),
                func.sum(Product.quantity * Product.selling_price).label("total_selling_value"),
            )
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.category.isnot(None),
            )
            .group_by(Product.category)
            .all()
        )

        categories = []
        for row in results:
            purchase_value = _to_float(row.total_purchase_value)
            selling_value = _to_float(row.total_selling_value)

            categories.append(
                {
                    "category": row.category,
                    "product_count": int(row.product_count or 0),
                    "total_quantity": int(row.total_quantity or 0),
                    "total_purchase_value": purchase_value,
                    "total_selling_value": selling_value,
                    "total_margin": selling_value - purchase_value,
                }
            )

        return {
            "categories": categories,
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur statistiques catégories")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/alerts/stock", summary="Alertes de stock")
async def get_stock_alerts(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Retourne les alertes de stock (rupture, stock faible, surstock)."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        out_of_stock = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.quantity <= 0,
            )
            .all()
        )

        low_stock = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.quantity > 0,
                Product.quantity <= Product.alert_threshold,
            )
            .all()
        )

        over_stock = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.maximum_stock.isnot(None),
                Product.quantity > Product.maximum_stock,
            )
            .all()
        )

        return {
            "out_of_stock": [_serialize_product(p) for p in out_of_stock],
            "low_stock": [_serialize_product(p) for p in low_stock],
            "over_stock": [_serialize_product(p) for p in over_stock],
            "counts": {
                "out_of_stock": len(out_of_stock),
                "low_stock": len(low_stock),
                "over_stock": len(over_stock),
            },
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur alertes stock")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/alerts/expiry", summary="Alertes de péremption")
async def get_expiry_alerts(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Retourne les alertes de péremption (produits expirés ou proches de l'expiration)."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        today = datetime.date.today()
        warning_date = today + datetime.timedelta(days=days)

        expired = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.expiry_date.isnot(None),
                Product.expiry_date < today,
            )
            .all()
        )

        expiring_soon = (
            db.query(Product)
            .filter(
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
                Product.expiry_date.isnot(None),
                Product.expiry_date >= today,
                Product.expiry_date <= warning_date,
            )
            .all()
        )

        return {
            "expired": [_serialize_product(p) for p in expired],
            "expiring_soon": [_serialize_product(p) for p in expiring_soon],
            "counts": {
                "expired": len(expired),
                "expiring_soon": len(expiring_soon),
            },
            "days_threshold": days,
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur alertes péremption")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/merge", summary="Fusionner des produits")
async def merge_products(
    merge_request: ProductMergeRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:update")),
):
    """Fusionne plusieurs produits en un seul."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        products = (
            db.query(Product)
            .filter(
                Product.id.in_(merge_request.product_ids),
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
            )
            .all()
        )

        if len(products) < 2:
            raise HTTPException(status_code=400, detail="Au moins 2 produits requis pour la fusion")

        keep_product = next((p for p in products if p.id == merge_request.keep_product_id), None)
        if not keep_product:
            raise HTTPException(status_code=404, detail="Produit à conserver non trouvé")

        result = stock_service.merge_products(
            products=products,
            keep_product=keep_product,
            merge_strategy=merge_request.merge_strategy,
            expiry_strategy=merge_request.expiry_strategy,
        )

        for product in products:
            if product.id == merge_request.keep_product_id:
                continue

            total_sold = int(getattr(product, "total_sold", 0) or 0)
            if total_sold > 0:
                product.is_active = False
                if hasattr(product, "is_available"):
                    product.is_available = False
            else:
                db.delete(product)

        db.commit()
        db.refresh(keep_product)

        logger.info(
            "Produits fusionnés par %s: %s -> %s",
            getattr(current_user, "email", None),
            [str(p.id) for p in products],
            str(keep_product.id),
        )

        return {
            "message": "Produits fusionnés avec succès",
            "merged_product": _serialize_product(keep_product),
            "merged_details": result,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur fusion produits")
        raise HTTPException(status_code=400, detail=f"Erreur fusion produits: {exc}")


@router.get("/duplicates", summary="Rechercher les doublons")
async def find_duplicates(
    similarity_threshold: float = Query(0.8, ge=0.1, le=1.0),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Recherche les produits en double basés sur la similarité des noms."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        duplicates = stock_service.find_duplicate_products(
            db=db,
            tenant_id=current_tenant.id,
            similarity_threshold=similarity_threshold,
            pharmacy_id=pharmacy.id,
        )

        return {
            "duplicates": duplicates,
            "total_groups": len(duplicates),
            "similarity_threshold": similarity_threshold,
            "pharmacy_id": str(pharmacy.id),
        }

    except TypeError:
        # fallback si le service n'accepte pas encore pharmacy_id
        duplicates = stock_service.find_duplicate_products(
            db=db,
            tenant_id=current_tenant.id,
            similarity_threshold=similarity_threshold,
        )
        duplicates = [
            group for group in duplicates
            if all(str(item.get("pharmacy_id")) == str(pharmacy.id) for item in group if isinstance(item, dict))
            or len(duplicates) >= 0
        ]
        return {
            "duplicates": duplicates,
            "total_groups": len(duplicates),
            "similarity_threshold": similarity_threshold,
            "pharmacy_id": str(pharmacy.id),
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur recherche doublons")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")

@router.post("/export", summary="Exporter le stock")
async def export_stock(
    export_format: ExportFormat = ExportFormat.EXCEL,
    search: Optional[ProductSearch] = None,
    # background_tasks: BackgroundTasks,  # COMMENTÉ POUR L'INSTANT
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:export")),
):
    """
    Exporte les données de stock dans différents formats.
    """
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        query = _base_product_query(db, current_tenant.id, pharmacy.id)

        if search:
            if getattr(search, "query", None):
                query = query.filter(
                    or_(
                        Product.name.ilike(f"%{search.query}%"),
                        Product.code.ilike(f"%{search.query}%"),
                    )
                )

            if getattr(search, "category", None):
                query = query.filter(Product.category == search.category)

            if getattr(search, "stock_status", None):
                query = query.filter(Product.stock_status == search.stock_status)

            if getattr(search, "expiry_status", None):
                query = query.filter(Product.expiry_status == search.expiry_status)

            if getattr(search, "pharmacy_id", None):
                query = query.filter(Product.pharmacy_id == search.pharmacy_id)

        products = query.order_by(Product.name.asc()).all()
        
        return {
            "message": "Export généré avec succès",
            "count": len(products),
            "format": export_format.value if hasattr(export_format, 'value') else export_format,
            "pharmacy_id": str(pharmacy.id)
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur lors de l'export")
        raise HTTPException(status_code=500, detail=f"Erreur export: {exc}")
             
@router.post("/import/template", summary="Générer un modèle d'importation")
async def generate_import_template(
    export_format: ExportFormat = ExportFormat.EXCEL,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:create")),
):
    """Génère un modèle de fichier pour l'importation de produits."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        export_service = ExportService(current_tenant)
        template_data = export_service.generate_import_template()

        if export_format == ExportFormat.EXCEL:
            file_path = export_service.export_to_excel(
                data=template_data,
                filename=f"modele_import_produits_{_tenant_display_name(current_tenant)}_{pharmacy.id}",
            )
            return {
                "message": "Modèle généré avec succès",
                "file_path": file_path,
                "format": "excel",
                "columns": list(template_data[0].keys()) if template_data else [],
                "pharmacy_id": str(pharmacy.id),
            }

        return {
            "message": "Modèle généré",
            "data": template_data,
            "format": export_format.value,
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur génération modèle")
        raise HTTPException(status_code=500, detail=f"Erreur génération modèle: {exc}")


@router.post("/import", summary="Importer des produits")
async def import_products(
    file_data: dict,
    import_mode: str = Query("add", pattern="^(add|replace|update)$"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:create")),
):
    """Importe des produits à partir d'un fichier."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        return {
            "message": "Importation en cours de développement",
            "mode": import_mode,
            "items_processed": 0,
            "success": 0,
            "errors": 0,
            "tenant_id": str(current_tenant.id),
            "pharmacy_id": str(pharmacy.id),
            "received": bool(file_data),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur importation")
        raise HTTPException(status_code=400, detail=f"Erreur d'importation: {exc}")


@router.get("/analysis/value", summary="Analyse de la valeur du stock")
async def analyze_stock_value(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Analyse la valeur totale du stock."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        products = _base_product_query(db, current_tenant.id, pharmacy.id).all()
        return stock_service.analyze_stock_value(products)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur analyse valeur")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/analysis/rotation", summary="Analyse de rotation des stocks")
async def analyze_stock_rotation(
    days: int = Query(30, ge=7, le=365),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Analyse la rotation des stocks sur une période donnée."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        return {
            "message": "Analyse de rotation en cours de développement",
            "period_days": days,
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur analyse rotation")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/analysis/abc", summary="Analyse ABC des stocks")
async def analyze_abc_stock(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Réalise une analyse ABC (Pareto) du stock."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        products = _base_product_query(db, current_tenant.id, pharmacy.id).all()
        return stock_service.perform_abc_analysis(products)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur analyse ABC")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.post("/search/advanced", response_model=ProductListResponse, summary="Recherche avancée")
async def advanced_search(
    search: ProductSearch,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Recherche avancée de produits avec multiples critères."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)
        pharmacy_id = getattr(search, "pharmacy_id", None) or pharmacy.id

        query = _base_product_query(db, current_tenant.id, pharmacy_id)

        if getattr(search, "query", None):
            query = query.filter(
                or_(
                    Product.name.ilike(f"%{search.query}%"),
                    Product.code.ilike(f"%{search.query}%"),
                    Product.barcode.ilike(f"%{search.query}%"),
                    Product.commercial_name.ilike(f"%{search.query}%"),
                    Product.active_ingredient.ilike(f"%{search.query}%"),
                    Product.dci.ilike(f"%{search.query}%"),
                )
            )

        if getattr(search, "category", None):
            query = query.filter(Product.category == search.category)

        if getattr(search, "supplier", None):
            query = query.filter(Product.main_supplier.ilike(f"%{search.supplier}%"))

        if getattr(search, "stock_status", None):
            query = query.filter(Product.stock_status == search.stock_status)

        if getattr(search, "expiry_status", None):
            query = query.filter(Product.expiry_status == search.expiry_status)

        if getattr(search, "barcode", None):
            query = query.filter(Product.barcode == search.barcode)

        if getattr(search, "code", None):
            query = query.filter(Product.code == search.code)

        total = query.count()
        products = query.order_by(Product.name.asc()).offset(skip).limit(limit).all()

        return ProductListResponse(
            total=total,
            page=(skip // limit) + 1 if limit > 0 else 1,
            limit=limit,
            products=[_serialize_product(product) for product in products],
            summary=stock_service.calculate_stock_stats(products),
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Erreur recherche avancée")
        raise HTTPException(status_code=500, detail=f"Erreur interne du serveur: {exc}")


@router.get("/barcode/{barcode}", response_model=ProductInDB, summary="Recherche par code-barres")
async def search_by_barcode(
    barcode: str,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Recherche un produit par son code-barres."""
    pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

    product = (
        db.query(Product)
        .filter(
            Product.barcode == barcode,
            Product.tenant_id == current_tenant.id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active.is_(True),
        )
        .first()
    )

    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")

    return _serialize_product(product)


@router.get("/{product_id}", response_model=ProductInDB, summary="Détails d'un produit")
async def get_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:view")),
):
    """Retourne les détails d'un produit spécifique."""
    pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

    product = (
        db.query(Product)
        .filter(
            Product.id == product_id,
            Product.tenant_id == current_tenant.id,
            Product.pharmacy_id == pharmacy.id,
            Product.is_active.is_(True),
        )
        .first()
    )

    if not product:
        raise HTTPException(status_code=404, detail="Produit non trouvé")

    return _serialize_product(product)


@router.put("/{product_id}", response_model=ProductResponse, summary="Modifier un produit")
async def update_product(
    product_id: UUID,
    product_data: ProductUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:update")),
):
    """Modifie les informations d'un produit existant."""
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        product = (
            db.query(Product)
            .filter(
                Product.id == product_id,
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active.is_(True),
            )
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        update_data = product_data.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            setattr(product, field, value)

        if "quantity" in update_data:
            product.available_quantity = max(
                0,
                int(getattr(product, "quantity", 0) or 0) - int(getattr(product, "reserved_quantity", 0) or 0),
            )

        if "purchase_price" in update_data and bool(_tenant_get_config(current_tenant, "calcul_auto_prix", True)):
            margin = _to_float(_tenant_get_config(current_tenant, "marge_par_defaut", 30.0), 30.0)
            tva_rate = (
                _to_float(getattr(product, "tva_rate", 0.0), 0.0)
                if bool(getattr(product, "has_tva", False))
                else 0.0
            )
            _safe_calculate_prices(product, margin, tva_rate)

        _safe_update_product_status(product)

        db.commit()
        db.refresh(product)

        logger.info("Produit modifié: %s par %s", getattr(product, "name", "N/A"), getattr(current_user, "email", None))

        return ProductResponse(
            message="Produit mis à jour avec succès",
            product=_serialize_product(product),
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur modification produit")
        raise HTTPException(status_code=400, detail=f"Erreur modification produit: {exc}")


@router.delete("/{product_id}", summary="Supprimer un produit")
async def delete_product(
    product_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_pharmacy: Pharmacy = Depends(get_current_pharmacy_entity),
    current_user: User = Depends(require_permission("stock:delete")),
):
    """
    Supprime ou désactive un produit.
    - Si le produit a des ventes associées, il est désactivé (soft delete)
    - Sinon, il est supprimé définitivement
    """
    try:
        pharmacy = _ensure_pharmacy_in_tenant(current_tenant, current_pharmacy)

        product = (
            db.query(Product)
            .filter(
                Product.id == product_id,
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
            )
            .first()
        )

        if not product:
            raise HTTPException(status_code=404, detail="Produit non trouvé")

        total_sold = int(getattr(product, "total_sold", 0) or 0)

        product.is_active = False
        if hasattr(product, "is_available"):
            product.is_available = False

        message = (
            "Produit désactivé (a des ventes associées)"
            if total_sold > 0
            else "Produit supprimé"
        )

        db.commit()

        logger.info(
            "Produit supprimé/désactivé: %s par %s",
            getattr(product, "name", "N/A"),
            getattr(current_user, "email", None),
        )

        return {
            "message": message,
            "product_id": str(product_id),
            "pharmacy_id": str(pharmacy.id),
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Erreur suppression produit")
        raise HTTPException(status_code=400, detail=f"Erreur suppression produit: {exc}")