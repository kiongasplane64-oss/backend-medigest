# app/services/inventory.py
import logging
from typing import Optional, Dict, Any
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.product import Product, StockMovement
from app.models.user import User

logger = logging.getLogger(__name__)

class InventoryService:
    """Service de gestion d'inventaire"""
    
    def __init__(self, db: Session, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
    
    def update_stock(self, product_id: UUID, quantity_change: int, 
                    reason: str, reference: Optional[str] = None,
                    reference_type: Optional[str] = None,
                    user_id: Optional[UUID] = None) -> Dict[str, Any]:
        """
        Met à jour le stock d'un produit
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            raise ValueError(f"Produit {product_id} non trouvé")
        
        old_quantity = product.quantity
        new_quantity = max(0, old_quantity + quantity_change)
        
        # Déterminer le type de mouvement
        if quantity_change > 0:
            movement_type = "purchase"
        elif quantity_change < 0:
            movement_type = "sale"
        else:
            movement_type = "adjustment"
        
        # Créer le mouvement de stock
        movement = StockMovement(
            tenant_id=self.tenant_id,
            product_id=product_id,
            quantity_before=old_quantity,
            quantity_after=new_quantity,
            quantity_change=quantity_change,
            movement_type=movement_type,
            reason=reason,
            reference_number=reference,
            reference_type=reference_type,
            created_by=user_id
        )
        
        # Mettre à jour le produit
        product.quantity = new_quantity
        
        # Mettre à jour les dates
        from datetime import datetime
        if quantity_change > 0:
            product.last_purchase_date = datetime.utcnow().date()
            product.total_purchased += quantity_change
        elif quantity_change < 0:
            product.last_sale_date = datetime.utcnow().date()
            product.total_sold += abs(quantity_change)
        
        self.db.add(movement)
        self.db.commit()
        
        logger.info(f"Stock mis à jour: {product.code} - {old_quantity} -> {new_quantity}")
        
        return {
            "product_id": product_id,
            "product_name": product.name,
            "old_quantity": old_quantity,
            "new_quantity": new_quantity,
            "movement_id": movement.id
        }
    
    def check_stock_availability(self, product_id: UUID, quantity: int) -> bool:
        """
        Vérifie la disponibilité du stock
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False
        
        return product.available_quantity >= quantity
    
    def reserve_stock(self, product_id: UUID, quantity: int) -> bool:
        """
        Réserve du stock pour une vente
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False
        
        return product.reserve(quantity)
    
    def release_stock(self, product_id: UUID, quantity: int) -> bool:
        """
        Libère du stock réservé
        """
        product = self.db.query(Product).filter(
            Product.id == product_id,
            Product.tenant_id == self.tenant_id
        ).first()
        
        if not product:
            return False
        
        return product.release(quantity)
    
    def get_low_stock_products(self, threshold_percentage: float = 0.3) -> list:
        """
        Récupère les produits en rupture ou stock critique
        """
        products = self.db.query(Product).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True
        ).all()
        
        low_stock = []
        for product in products:
            if product.quantity <= 0:
                low_stock.append({
                    "product": product,
                    "status": "out_of_stock",
                    "alert_level": "high"
                })
            elif product.alert_threshold > 0 and product.quantity <= product.alert_threshold:
                low_stock.append({
                    "product": product,
                    "status": "low_stock",
                    "alert_level": "medium" if product.quantity > 0 else "high"
                })
            elif product.maximum_stock and product.quantity >= product.maximum_stock:
                low_stock.append({
                    "product": product,
                    "status": "over_stock",
                    "alert_level": "low"
                })
        
        return low_stock
    
    def get_expiring_products(self, days: int = 30) -> list:
        """
        Récupère les produits qui vont bientôt expirer
        """
        from datetime import datetime, timedelta
        
        expiry_date = datetime.utcnow().date() + timedelta(days=days)
        
        products = self.db.query(Product).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True,
            Product.expiry_date != None,
            Product.expiry_date <= expiry_date,
            Product.expiry_date >= datetime.utcnow().date()
        ).order_by(Product.expiry_date).all()
        
        expiring = []
        for product in products:
            days_remaining = (product.expiry_date - datetime.utcnow().date()).days
            
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
                "status": status,
                "days_remaining": days_remaining,
                "alert_level": alert_level
            })
        
        return expiring
    
    def calculate_stock_value(self) -> Dict[str, float]:
        """
        Calcule la valeur totale du stock
        """
        products = self.db.query(Product).filter(
            Product.tenant_id == self.tenant_id,
            Product.is_active == True
        ).all()
        
        total_purchase = sum(p.purchase_value for p in products)
        total_selling = sum(p.selling_value for p in products)
        total_margin = sum(p.margin_total for p in products)
        
        return {
            "purchase_value": total_purchase,
            "selling_value": total_selling,
            "potential_margin": total_margin,
            "margin_percentage": (total_margin / total_purchase * 100) if total_purchase > 0 else 0
        }