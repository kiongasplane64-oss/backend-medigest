# app/services/inventory.py
import logging
from typing import Optional, Dict, Any, List, Tuple
from uuid import UUID
from decimal import Decimal
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, distinct

from app.models.product import Product, ProductStock
from app.models.stock_movement import StockMovement
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.sale import Sale, SaleItem

logger = logging.getLogger(__name__)


class InventoryService:
    """Service de gestion d'inventaire avec communication avec les ventes"""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    # ============================================
    # GESTION DU STOCK
    # ============================================
    
    def update_stock(
        self,
        product_id: UUID,
        pharmacy_id: UUID,
        quantity_change: Decimal,
        reason: str,
        reference: Optional[str] = None,
        batch_number: Optional[str] = None,
        cost_price: Optional[Decimal] = None,
        selling_price: Optional[Decimal] = None,
        sale_id: Optional[UUID] = None,
        sale_item_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None
    ) -> StockMovement:
        """
        Met à jour le stock et enregistre le mouvement
        Ajout des liens avec les ventes pour traçabilité complète
        """
        # Récupérer le produit
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            raise ValueError(f"Produit {product_id} non trouvé")
        
        # Récupérer le stock actuel
        stock = self.db.query(ProductStock).filter(
            ProductStock.product_id == product_id,
            ProductStock.pharmacy_id == pharmacy_id,
            ProductStock.tenant_id == self.tenant_id
        ).first()
        
        quantity_before = Decimal(str(stock.quantity)) if stock else Decimal('0')
        quantity_after = quantity_before + quantity_change
        
        # S'assurer que le stock ne devient pas négatif
        if quantity_after < 0:
            raise ValueError(
                f"Stock insuffisant pour {product.name}. "
                f"Disponible: {quantity_before}, Demandé: {abs(quantity_change)}"
            )
        
        # Créer ou mettre à jour le stock
        if stock:
            stock.quantity = quantity_after
            stock.updated_at = datetime.utcnow()
            if user_id:
                stock.updated_by = user_id
        else:
            stock = ProductStock(
                tenant_id=self.tenant_id,
                product_id=product_id,
                pharmacy_id=pharmacy_id,
                quantity=quantity_after,
                created_at=datetime.utcnow(),
                created_by=user_id
            )
            self.db.add(stock)
        
        # Déterminer le type de mouvement
        if reason == "vente":
            movement_type = "sale"
        elif reason == "annulation_vente":
            movement_type = "sale_cancellation"
        elif reason == "achat":
            movement_type = "purchase"
        elif reason == "retour":
            movement_type = "return"
        elif reason == "transfert":
            movement_type = "transfer"
        elif reason == "inventaire":
            movement_type = "inventory_count"
        elif reason == "perte":
            movement_type = "loss"
        else:
            movement_type = "adjustment"
        
        # Créer le mouvement de stock
        movement = StockMovement(
            tenant_id=self.tenant_id,
            pharmacy_id=pharmacy_id,
            product_id=product_id,
            product_name=product.name,
            product_code=product.code,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            quantity_change=quantity_change,
            movement_type=movement_type,
            reason=reason,
            reference=reference,
            batch_number=batch_number,
            cost_price=cost_price or product.purchase_price,
            selling_price=selling_price or product.selling_price,
            sale_id=sale_id,
            sale_item_id=sale_item_id,
            created_at=datetime.utcnow(),
            created_by=user_id
        )
        
        self.db.add(movement)
        self.db.flush()
        
        # Mettre à jour les compteurs du produit
        if reason == "vente":
            product.total_sold = (product.total_sold or 0) + abs(quantity_change)
            product.last_sale_date = datetime.utcnow().date()
        elif reason == "achat":
            product.total_purchased = (product.total_purchased or 0) + abs(quantity_change)
            product.last_purchase_date = datetime.utcnow().date()
        
        self.db.commit()
        
        logger.info(
            f"Stock mis à jour: {product.code} - {quantity_before} -> {quantity_after} "
            f"(variation: {quantity_change}) - Raison: {reason}"
        )
        
        return movement
    
    def check_stock_availability(
        self,
        product_id: UUID,
        pharmacy_id: UUID,
        quantity: Decimal,
        include_reserved: bool = True
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Vérifie la disponibilité du stock avec détails
        Retourne (disponible, details)
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False, {
                "available": False,
                "reason": "Produit non trouvé",
                "product_id": str(product_id)
            }
        
        stock = self.db.query(ProductStock).filter(
            ProductStock.product_id == product_id,
            ProductStock.pharmacy_id == pharmacy_id,
            ProductStock.tenant_id == self.tenant_id
        ).first()
        
        available_quantity = stock.quantity if stock else 0
        
        if include_reserved:
            # Calculer les quantités réservées (ventes en cours)
            reserved = self.db.query(
                func.coalesce(func.sum(SaleItem.quantity), 0)
            ).join(Sale).filter(
                SaleItem.product_id == product_id,
                SaleItem.tenant_id == self.tenant_id,
                Sale.pharmacy_id == pharmacy_id,
                Sale.status == "pending"
            ).scalar() or 0
            
            available_quantity -= reserved
        
        is_available = available_quantity >= quantity
        
        details = {
            "available": is_available,
            "product_id": str(product_id),
            "product_name": product.name,
            "product_code": product.code,
            "requested_quantity": float(quantity),
            "available_quantity": float(available_quantity),
            "current_stock": float(stock.quantity) if stock else 0,
            "unit": product.unit
        }
        
        if not is_available:
            details["reason"] = "Stock insuffisant"
            details["shortage"] = float(quantity - available_quantity)
        
        return is_available, details
    
    def reserve_stock(
        self,
        product_id: UUID,
        pharmacy_id: UUID,
        quantity: Decimal,
        sale_item_id: UUID
    ) -> bool:
        """
        Réserve du stock pour une vente en cours
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False
        
        stock = self.db.query(ProductStock).filter(
            ProductStock.product_id == product_id,
            ProductStock.pharmacy_id == pharmacy_id,
            ProductStock.tenant_id == self.tenant_id
        ).first()
        
        if not stock or stock.quantity < quantity:
            return False
        
        # Stock réservé (champ à ajouter au modèle ProductStock)
        stock.reserved_quantity = (stock.reserved_quantity or 0) + quantity
        stock.updated_at = datetime.utcnow()
        
        self.db.commit()
        
        logger.info(f"Stock réservé: {product.code} - {quantity} pour sale_item {sale_item_id}")
        
        return True
    
    def release_stock(
        self,
        product_id: UUID,
        pharmacy_id: UUID,
        quantity: Decimal,
        sale_item_id: UUID
    ) -> bool:
        """
        Libère du stock réservé (annulation de vente)
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False
        
        stock = self.db.query(ProductStock).filter(
            ProductStock.product_id == product_id,
            ProductStock.pharmacy_id == pharmacy_id,
            ProductStock.tenant_id == self.tenant_id
        ).first()
        
        if not stock:
            return False
        
        stock.reserved_quantity = max(0, (stock.reserved_quantity or 0) - quantity)
        stock.updated_at = datetime.utcnow()
        
        self.db.commit()
        
        logger.info(f"Stock libéré: {product.code} - {quantity} pour sale_item {sale_item_id}")
        
        return True
    
    # ============================================
    # RAPPORTS ET ANALYSES
    # ============================================
    
    def get_low_stock_products(
        self,
        pharmacy_id: Optional[UUID] = None,
        threshold_percentage: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        Récupère les produits en rupture ou stock critique
        """
        query = self.db.query(Product, ProductStock).outerjoin(
            ProductStock,
            and_(
                Product.id == ProductStock.product_id,
                Product.tenant_id == ProductStock.tenant_id,
                ProductStock.pharmacy_id == pharmacy_id if pharmacy_id else True
            )
        ).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True
        )
        
        if pharmacy_id:
            query = query.filter(ProductStock.pharmacy_id == pharmacy_id)
        
        results = query.all()
        
        low_stock = []
        for product, stock in results:
            current_stock = stock.quantity if stock else 0
            reserved = stock.reserved_quantity if stock else 0
            available = current_stock - reserved
            
            if available <= 0:
                low_stock.append({
                    "product": product,
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "product_code": product.code,
                    "current_stock": float(current_stock),
                    "reserved": float(reserved),
                    "available": float(available),
                    "alert_threshold": product.alert_threshold,
                    "status": "out_of_stock",
                    "alert_level": "high"
                })
            elif product.alert_threshold > 0 and available <= product.alert_threshold:
                low_stock.append({
                    "product": product,
                    "product_id": str(product.id),
                    "product_name": product.name,
                    "product_code": product.code,
                    "current_stock": float(current_stock),
                    "reserved": float(reserved),
                    "available": float(available),
                    "alert_threshold": product.alert_threshold,
                    "status": "low_stock",
                    "alert_level": "medium" if available > 0 else "high"
                })
        
        return low_stock
    
    def get_expiring_products(
        self,
        pharmacy_id: Optional[UUID] = None,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Récupère les produits qui vont bientôt expirer
        """
        expiry_date = datetime.utcnow().date() + timedelta(days=days)
        
        query = self.db.query(Product, ProductStock).outerjoin(
            ProductStock,
            and_(
                Product.id == ProductStock.product_id,
                Product.tenant_id == ProductStock.tenant_id,
                ProductStock.pharmacy_id == pharmacy_id if pharmacy_id else True
            )
        ).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True,
            Product.expiry_date != None,
            Product.expiry_date <= expiry_date
        ).order_by(Product.expiry_date)
        
        if pharmacy_id:
            query = query.filter(ProductStock.pharmacy_id == pharmacy_id)
        
        results = query.all()
        
        expiring = []
        today = datetime.utcnow().date()
        
        for product, stock in results:
            days_remaining = (product.expiry_date - today).days
            current_stock = stock.quantity if stock else 0
            
            if days_remaining < 0:
                status = "expired"
                alert_level = "high"
            elif days_remaining <= 7:
                status = "critical"
                alert_level = "high"
            elif days_remaining <= 30:
                status = "warning"
                alert_level = "medium"
            else:
                status = "ok"
                alert_level = "low"
            
            expiring.append({
                "product": product,
                "product_id": str(product.id),
                "product_name": product.name,
                "product_code": product.code,
                "current_stock": float(current_stock),
                "expiry_date": product.expiry_date,
                "days_remaining": days_remaining,
                "status": status,
                "alert_level": alert_level,
                "batch_number": product.batch_number
            })
        
        return expiring
    
    def calculate_stock_value(
        self,
        pharmacy_id: Optional[UUID] = None,
        valuation_method: str = "purchase"
    ) -> Dict[str, Any]:
        """
        Calcule la valeur totale du stock
        """
        query = self.db.query(Product, ProductStock).join(
            ProductStock,
            and_(
                Product.id == ProductStock.product_id,
                Product.tenant_id == ProductStock.tenant_id,
                ProductStock.pharmacy_id == pharmacy_id if pharmacy_id else True
            )
        ).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True
        )
        
        if pharmacy_id:
            query = query.filter(ProductStock.pharmacy_id == pharmacy_id)
        
        results = query.all()
        
        total_purchase = Decimal('0')
        total_selling = Decimal('0')
        total_margin = Decimal('0')
        product_details = []
        
        for product, stock in results:
            quantity = stock.quantity if stock else 0
            reserved = stock.reserved_quantity if stock else 0
            available = quantity - reserved
            
            purchase_price = product.purchase_price or Decimal('0')
            selling_price = product.selling_price or Decimal('0')
            
            purchase_value = purchase_price * quantity
            selling_value = selling_price * quantity
            margin_value = selling_value - purchase_value
            
            total_purchase += purchase_value
            total_selling += selling_value
            total_margin += margin_value
            
            product_details.append({
                "product_id": str(product.id),
                "product_name": product.name,
                "product_code": product.code,
                "quantity": float(quantity),
                "reserved": float(reserved),
                "available": float(available),
                "purchase_price": float(purchase_price),
                "selling_price": float(selling_price),
                "purchase_value": float(purchase_value),
                "selling_value": float(selling_value),
                "margin": float(margin_value)
            })
        
        margin_percentage = (total_margin / total_purchase * 100) if total_purchase > 0 else 0
        
        return {
            "total_purchase_value": float(total_purchase),
            "total_selling_value": float(total_selling),
            "potential_margin": float(total_margin),
            "margin_percentage": float(margin_percentage),
            "product_count": len(product_details),
            "products": product_details[:20]  # Limiter pour la performance
        }
    
    # ============================================
    # COMMUNICATION AVEC LE MODULE VENTES
    # ============================================
    
    def get_sales_impact_on_stock(
        self,
        product_id: Optional[UUID] = None,
        pharmacy_id: Optional[UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Récupère l'impact des ventes sur le stock
        Point de communication avec le module sales
        """
        # Construire la requête
        query = self.db.query(
            Product.id.label("product_id"),
            Product.code,
            Product.name,
            Product.unit,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("total_sold"),
            func.coalesce(func.sum(SaleItem.total), 0).label("total_revenue"),
            func.count(distinct(Sale.id)).label("sale_count"),
            func.avg(SaleItem.unit_price).label("average_price")
        ).join(
            SaleItem, SaleItem.product_id == Product.id
        ).join(
            Sale, Sale.id == SaleItem.sale_id
        ).filter(
            Product.tenant_id == self.tenant_id,
            Sale.tenant_id == self.tenant_id,
            Sale.status == "completed"
        )
        
        if product_id:
            query = query.filter(Product.id == product_id)
        
        if pharmacy_id:
            query = query.filter(Sale.pharmacy_id == pharmacy_id)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        results = query.group_by(
            Product.id, Product.code, Product.name, Product.unit
        ).order_by(
            desc("total_sold")
        ).limit(limit).all()
        
        # Ajouter les informations de stock actuelles
        impacts = []
        for row in results:
            # Récupérer le stock actuel
            stock = self.db.query(ProductStock).filter(
                ProductStock.product_id == row.product_id,
                ProductStock.pharmacy_id == pharmacy_id if pharmacy_id else True,
                ProductStock.tenant_id == self.tenant_id
            ).first()
            
            impacts.append({
                "product_id": str(row.product_id),
                "product_code": row.code,
                "product_name": row.name,
                "unit": row.unit,
                "total_sold": int(row.total_sold),
                "total_revenue": float(row.total_revenue),
                "sale_count": int(row.sale_count),
                "average_price": float(row.average_price),
                "current_stock": float(stock.quantity) if stock else 0,
                "stock_impact": -int(row.total_sold)
            })
        
        return impacts
    
    def get_movements_by_sale(
        self,
        sale_id: UUID
    ) -> List[StockMovement]:
        """
        Récupère tous les mouvements de stock liés à une vente
        """
        movements = self.db.query(StockMovement).filter(
            StockMovement.sale_id == sale_id,
            StockMovement.tenant_id == self.tenant_id,
            StockMovement.movement_type == "sale"
        ).order_by(StockMovement.created_at).all()
        
        return movements
    
    def get_movements_by_sale_item(
        self,
        sale_item_id: UUID
    ) -> Optional[StockMovement]:
        """
        Récupère le mouvement de stock lié à un item de vente
        """
        movement = self.db.query(StockMovement).filter(
            StockMovement.sale_item_id == sale_item_id,
            StockMovement.tenant_id == self.tenant_id
        ).first()
        
        return movement
    
    # ============================================
    # RAPPORTS D'INVENTAIRE
    # ============================================
    
    def get_stock_turnover_rate(
        self,
        product_id: Optional[UUID] = None,
        pharmacy_id: Optional[UUID] = None,
        days: int = 365
    ) -> Dict[str, Any]:
        """
        Calcule le taux de rotation du stock
        """
        from_date = datetime.utcnow().date() - timedelta(days=days)
        
        # Ventes sur la période
        sales_query = self.db.query(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("total_sold")
        ).join(Sale).filter(
            SaleItem.tenant_id == self.tenant_id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= from_date
        )
        
        if product_id:
            sales_query = sales_query.filter(SaleItem.product_id == product_id)
        if pharmacy_id:
            sales_query = sales_query.filter(Sale.pharmacy_id == pharmacy_id)
        
        sales_by_product = {
            str(row.product_id): int(row.total_sold)
            for row in sales_query.group_by(SaleItem.product_id).all()
        }
        
        # Stock actuel
        stock_query = self.db.query(ProductStock).filter(
            ProductStock.tenant_id == self.tenant_id
        )
        
        if pharmacy_id:
            stock_query = stock_query.filter(ProductStock.pharmacy_id == pharmacy_id)
        
        stock_by_product = {}
        for stock in stock_query.all():
            stock_by_product[str(stock.product_id)] = float(stock.quantity)
        
        # Calculer les taux
        results = []
        all_product_ids = set(sales_by_product.keys()) | set(stock_by_product.keys())
        
        for product_id_str in all_product_ids:
            total_sold = sales_by_product.get(product_id_str, 0)
            current_stock = stock_by_product.get(product_id_str, 0)
            
            turnover_rate = total_sold / current_stock if current_stock > 0 else 0
            
            product = self.db.query(Product).filter(
                Product.id == UUID(product_id_str),
                Product.tenant_id == self.tenant_id
            ).first()
            
            if product:
                results.append({
                    "product_id": product_id_str,
                    "product_name": product.name,
                    "product_code": product.code,
                    "total_sold": total_sold,
                    "current_stock": current_stock,
                    "turnover_rate": round(turnover_rate, 2),
                    "days_of_stock": round(365 / turnover_rate, 1) if turnover_rate > 0 else float('inf')
                })
        
        # Trier par taux de rotation (décroissant)
        results.sort(key=lambda x: x["turnover_rate"], reverse=True)
        
        return {
            "period_days": days,
            "total_products": len(results),
            "average_turnover_rate": round(
                sum(r["turnover_rate"] for r in results) / len(results), 2
            ) if results else 0,
            "products": results[:50]
        }
    
    def get_stock_movements_report(
        self,
        pharmacy_id: Optional[UUID] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        movement_type: Optional[str] = None,
        product_id: Optional[UUID] = None,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Génère un rapport des mouvements de stock
        """
        query = self.db.query(StockMovement).filter(
            StockMovement.tenant_id == self.tenant_id
        )
        
        if pharmacy_id:
            query = query.filter(StockMovement.pharmacy_id == pharmacy_id)
        
        if start_date:
            query = query.filter(func.date(StockMovement.created_at) >= start_date)
        if end_date:
            query = query.filter(func.date(StockMovement.created_at) <= end_date)
        
        if movement_type:
            query = query.filter(StockMovement.movement_type == movement_type)
        
        if product_id:
            query = query.filter(StockMovement.product_id == product_id)
        
        movements = query.order_by(
            desc(StockMovement.created_at)
        ).limit(limit).all()
        
        return [
            {
                "id": str(m.id),
                "product_id": str(m.product_id),
                "product_name": m.product_name,
                "product_code": m.product_code,
                "pharmacy_id": str(m.pharmacy_id),
                "quantity_before": float(m.quantity_before),
                "quantity_after": float(m.quantity_after),
                "quantity_change": float(m.quantity_change),
                "movement_type": m.movement_type,
                "reason": m.reason,
                "reference": m.reference,
                "batch_number": m.batch_number,
                "cost_price": float(m.cost_price) if m.cost_price else None,
                "selling_price": float(m.selling_price) if m.selling_price else None,
                "sale_id": str(m.sale_id) if m.sale_id else None,
                "created_at": m.created_at.isoformat(),
                "created_by": str(m.created_by) if m.created_by else None
            }
            for m in movements
        ]
    
    # ============================================
    # ALERTES ET NOTIFICATIONS
    # ============================================
    
    def get_stock_alerts(
        self,
        pharmacy_id: Optional[UUID] = None
    ) -> Dict[str, Any]:
        """
        Récupère toutes les alertes de stock
        """
        low_stock = self.get_low_stock_products(pharmacy_id)
        expiring = self.get_expiring_products(pharmacy_id, days=30)
        
        # Alertes critiques
        critical_alerts = []
        warning_alerts = []
        info_alerts = []
        
        for item in low_stock:
            if item["alert_level"] == "high":
                critical_alerts.append({
                    "type": "low_stock",
                    "product": item["product_name"],
                    "message": f"Rupture de stock: {item['product_name']}",
                    "details": item
                })
            elif item["alert_level"] == "medium":
                warning_alerts.append({
                    "type": "low_stock",
                    "product": item["product_name"],
                    "message": f"Stock bas: {item['product_name']} (plus que {item['available']})",
                    "details": item
                })
        
        for item in expiring:
            if item["alert_level"] == "high":
                critical_alerts.append({
                    "type": "expiry",
                    "product": item["product_name"],
                    "message": f"Produit expiré ou critique: {item['product_name']}",
                    "details": item
                })
            elif item["alert_level"] == "medium":
                warning_alerts.append({
                    "type": "expiry",
                    "product": item["product_name"],
                    "message": f"Produit expire dans {item['days_remaining']} jours: {item['product_name']}",
                    "details": item
                })
        
        return {
            "critical_alerts": critical_alerts,
            "warning_alerts": warning_alerts,
            "info_alerts": info_alerts,
            "total_critical": len(critical_alerts),
            "total_warnings": len(warning_alerts)
        }
    
    # ============================================
    # PRÉVISIONS
    # ============================================
    
    def get_reorder_suggestions(
        self,
        pharmacy_id: Optional[UUID] = None,
        safety_days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Génère des suggestions de réapprovisionnement
        basées sur les ventes passées et les délais de livraison
        """
        # Récupérer les ventes des 90 derniers jours
        sales_period = 90
        from_date = datetime.utcnow().date() - timedelta(days=sales_period)
        
        # Calculer les ventes moyennes par produit
        sales_query = self.db.query(
            SaleItem.product_id,
            func.sum(SaleItem.quantity).label("total_sold"),
            func.count(distinct(Sale.id)).label("sale_count")
        ).join(Sale).filter(
            SaleItem.tenant_id == self.tenant_id,
            Sale.status == "completed",
            func.date(Sale.created_at) >= from_date
        )
        
        if pharmacy_id:
            sales_query = sales_query.filter(Sale.pharmacy_id == pharmacy_id)
        
        sales_by_product = {}
        for row in sales_query.group_by(SaleItem.product_id).all():
            sales_by_product[str(row.product_id)] = {
                "total_sold": int(row.total_sold),
                "sale_count": int(row.sale_count),
                "avg_daily": int(row.total_sold) / sales_period
            }
        
        # Récupérer les stocks actuels
        stock_query = self.db.query(ProductStock).filter(
            ProductStock.tenant_id == self.tenant_id
        )
        
        if pharmacy_id:
            stock_query = stock_query.filter(ProductStock.pharmacy_id == pharmacy_id)
        
        stock_by_product = {
            str(stock.product_id): {
                "quantity": float(stock.quantity),
                "reserved": float(stock.reserved_quantity or 0)
            }
            for stock in stock_query.all()
        }
        
        # Récupérer les produits
        products = self.db.query(Product).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True
        ).all()
        
        suggestions = []
        
        for product in products:
            product_id_str = str(product.id)
            sales_data = sales_by_product.get(product_id_str, {"avg_daily": 0})
            stock_data = stock_by_product.get(product_id_str, {"quantity": 0, "reserved": 0})
            
            avg_daily = sales_data["avg_daily"]
            current_stock = stock_data["quantity"]
            reserved = stock_data["reserved"]
            available = current_stock - reserved
            
            # Calculer le stock de sécurité
            lead_time_days = product.lead_time_days or 7
            safety_stock = avg_daily * lead_time_days
            reorder_point = safety_stock
            
            # Calculer la quantité à commander
            if available < reorder_point:
                recommended_qty = max(
                    product.minimum_order_quantity or 0,
                    safety_stock * 2 - available
                )
                
                suggestions.append({
                    "product_id": product_id_str,
                    "product_name": product.name,
                    "product_code": product.code,
                    "current_stock": current_stock,
                    "reserved": reserved,
                    "available": available,
                    "avg_daily_sales": round(avg_daily, 2),
                    "lead_time_days": lead_time_days,
                    "reorder_point": round(reorder_point, 2),
                    "recommended_quantity": max(0, round(recommended_qty)),
                    "priority": "high" if available < reorder_point / 2 else "medium",
                    "supplier": product.main_supplier
                })
        
        # Trier par priorité
        suggestions.sort(key=lambda x: 0 if x["priority"] == "high" else 1)
        
        return suggestions