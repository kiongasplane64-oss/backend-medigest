# app/api/v1/sales.py
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session, selectinload, joinedload
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, date, timedelta
import logging
from decimal import Decimal
import json

from app.db.session import get_db
from app.models.sale import Sale, SaleItem
from app.models.product import Product, ProductStock
from app.models.client import Client
from app.models.user import User
from app.models.pharmacy import Pharmacy
from app.models.user_pharmacy import UserPharmacy
from app.models.tenant import Tenant
from app.schemas.sale import (
    SaleCreate, SaleResponse, SaleInDB, SaleUpdate, SaleFilter,
    DailyStats, SaleListResponse, QuickSaleRequest, SaleRefundRequest,
    CreditSaleCreate, ReceiptData, SaleItemCreate, SaleDetailResponse,
    SalesStatsResponse, PharmacyStats, SaleExportRequest, SaleExportResponse,
    UserPharmacyAccess, SaleValidationRequest, SaleItemResponse
)
from app.schemas.client import ClientCreate, ClientInDB
from app.api.deps import (
    get_current_tenant, 
    get_current_user, 
    get_current_active_user, 
    require_role, 
    require_permission
)
from app.core.security import require_permission, require_role
from app.services.inventory import InventoryService
from app.services.reporting import ReportService
from app.services.notification_service import NotificationService
from app.services.receipt import ReceiptService
from app.core.config import settings
from app.utils.pagination import paginate
from app.utils.export import export_to_excel
from app.utils.validators import validate_stock_availability

router = APIRouter(prefix="/sales", tags=["Ventes"])
logger = logging.getLogger(__name__)

# =======================
# Tâches en arrière-plan
# =======================
async def generate_sale_receipt(sale_id: UUID, tenant_id: UUID, pharmacy_id: UUID, db: Session):
    """Génère un reçu PDF pour la vente"""
    try:
        receipt_service = ReceiptService(db)
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == pharmacy_id
        ).first()
        
        if sale:
            receipt_path = await receipt_service.generate_sale_receipt(sale)
            sale.receipt_path = receipt_path
            db.commit()
            logger.info(f"Reçu généré pour la vente {sale.reference}: {receipt_path}")
    except Exception as e:
        logger.error(f"Erreur génération reçu pour vente {sale_id}: {str(e)}")

async def send_sale_notification(sale_id: UUID, tenant_id: UUID, pharmacy_id: UUID, db: Session):
    """Envoie des notifications pour la vente"""
    try:
        notification_service = NotificationService(db)
        sale = db.query(Sale).options(
            joinedload(Sale.client),
            joinedload(Sale.creator),
            joinedload(Sale.pharmacy)
        ).filter(
            Sale.id == sale_id,
            Sale.tenant_id == tenant_id,
            Sale.pharmacy_id == pharmacy_id
        ).first()
        
        if sale:
            # Notification au vendeur
            await notification_service.send_sale_confirmation(sale)
            
            # Notification au client si email/téléphone disponible
            if sale.client and (sale.client.email or sale.client.telephone):
                await notification_service.send_customer_receipt(sale)
                
            logger.info(f"Notifications envoyées pour la vente {sale.reference}")
    except Exception as e:
        logger.error(f"Erreur notification pour vente {sale_id}: {str(e)}")

async def update_sales_statistics(tenant_id: UUID, pharmacy_id: UUID, sale_date: date, db: Session):
    """Met à jour les statistiques de ventes"""
    try:
        report_service = ReportService(db)
        await report_service.update_daily_sales_stats(tenant_id, pharmacy_id, sale_date)
        logger.info(f"Statistiques mises à jour pour {sale_date}")
    except Exception as e:
        logger.error(f"Erreur mise à jour statistiques: {str(e)}")

# =======================
# Helpers
# =======================
def get_user_accessible_pharmacies(db: Session, user_id: UUID, tenant_id: UUID) -> List[UUID]:
    """Récupère la liste des pharmacies accessibles par l'utilisateur"""
    if user_id:
        accessible_pharmacies = db.query(UserPharmacy.pharmacy_id).filter(
            UserPharmacy.user_id == user_id
        ).all()
        return [p.pharmacy_id for p in accessible_pharmacies]
    return []

def get_current_pharmacy(
    db: Session, 
    user_id: UUID, 
    tenant_id: UUID,
    requested_pharmacy_id: Optional[UUID] = None
) -> Pharmacy:
    """Détermine la pharmacie courante pour l'utilisateur"""
    # Si une pharmacie est spécifiée, vérifier l'accès
    if requested_pharmacy_id:
        user_pharmacy = db.query(UserPharmacy).filter(
            UserPharmacy.user_id == user_id,
            UserPharmacy.pharmacy_id == requested_pharmacy_id
        ).first()
        
        if not user_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette pharmacie"
            )
        
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == requested_pharmacy_id,
            Pharmacy.tenant_id == tenant_id,
            Pharmacy.is_active == True
        ).first()
    else:
        # Utiliser la pharmacie principale de l'utilisateur
        user_pharmacy = db.query(UserPharmacy).filter(
            UserPharmacy.user_id == user_id,
            UserPharmacy.is_primary == True
        ).first()
        
        if not user_pharmacy:
            # Prendre la première pharmacie accessible
            user_pharmacy = db.query(UserPharmacy).filter(
                UserPharmacy.user_id == user_id
            ).first()
        
        if not user_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucune pharmacie configurée pour l'utilisateur"
            )
        
        pharmacy = db.query(Pharmacy).filter(
            Pharmacy.id == user_pharmacy.pharmacy_id,
            Pharmacy.tenant_id == tenant_id,
            Pharmacy.is_active == True
        ).first()
    
    if not pharmacy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pharmacie non trouvée ou inactive"
        )
    
    return pharmacy

# =======================
# Routes principales
# =======================
@router.post("/", response_model=SaleResponse, status_code=status.HTTP_201_CREATED)
@require_permission("sales:create")
async def create_sale(
    sale_data: SaleCreate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Crée une nouvelle vente avec gestion complète par pharmacie
    """
    try:
        # Vérifier le rôle
        if current_user.role not in ["admin", "vendeur", "gerant", "caissier"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Rôle insuffisant pour créer une vente"
            )

        # Déterminer la pharmacie
        pharmacy = get_current_pharmacy(
            db=db,
            user_id=current_user.id,
            tenant_id=current_tenant.id,
            requested_pharmacy_id=sale_data.pharmacy_id
        )

        # Validation des données
        if not sale_data.items or len(sale_data.items) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La vente doit contenir au moins un article"
            )

        # Vérification client
        client = None
        if sale_data.client_id:
            client = db.query(Client).filter(
                Client.id == sale_data.client_id,
                Client.tenant_id == current_tenant.id,
                Client.is_active == True
            ).first()
            
            if not client:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Client non trouvé ou inactif"
                )
            
            # Vérification crédit client
            if sale_data.is_credit:
                if not getattr(client, 'eligible_credit', False):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Client non éligible au crédit"
                    )
                
                credit_limit = getattr(client, 'credit_limit', 0)
                current_debt = getattr(client, 'dette_actuelle', 0)
                credit_available = credit_limit - current_debt
                
                if sale_data.total_amount > credit_available:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Crédit insuffisant. Disponible: {credit_available:.2f}, Requis: {sale_data.total_amount:.2f}"
                    )

        # Vérification des stocks par pharmacie
        inventory_service = InventoryService(db, current_tenant.id)
        unavailable_items = []
        
        for item in sale_data.items:
            # Produit spécifique à la pharmacie
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.tenant_id == current_tenant.id,
                Product.pharmacy_id == pharmacy.id,
                Product.is_active == True
            ).first()
            
            if not product:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Produit {item.product_id} non trouvé dans la pharmacie {pharmacy.name}"
                )
            
            # Vérification stock
            available_stock = getattr(product, 'quantity', 0)
            if available_stock < item.quantity:
                unavailable_items.append({
                    "product": getattr(product, 'name', 'Unknown'),
                    "requested": item.quantity,
                    "available": available_stock,
                    "pharmacy": pharmacy.name
                })
        
        if unavailable_items:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Stock insuffisant pour certains articles",
                    "unavailable_items": unavailable_items,
                    "pharmacy": pharmacy.name
                }
            )

        # Génération référence
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        reference = f"VNT-{timestamp}-{pharmacy.pharmacy_code}"

        # Création de la vente
        sale = Sale(
            tenant_id=current_tenant.id,
            pharmacy_id=pharmacy.id,
            reference=reference,
            client_id=sale_data.client_id,
            client_name=client.nom_complet if client else sale_data.client_name,
            client_phone=client.telephone if client else sale_data.client_phone,
            created_by=current_user.id,
            seller_name=current_user.nom_complet,
            payment_method=sale_data.payment_method.value,
            reference_payment=sale_data.reference_payment,
            payment_date=datetime.utcnow() if sale_data.payment_method.value != "credit" else None,
            is_credit=sale_data.is_credit,
            credit_due_date=sale_data.credit_due_date,
            guarantee_deposit=sale_data.guarantee_deposit or Decimal('0.00'),
            guarantor_name=sale_data.guarantor_name,
            guarantor_phone=sale_data.guarantor_phone,
            global_discount=sale_data.global_discount or Decimal('0.00'),
            notes=sale_data.notes,
            subtotal=sale_data.subtotal,
            total_discount=sale_data.total_discount,
            total_tva=sale_data.total_tva,
            total_amount=sale_data.total_amount,
            status="pending" if sale_data.is_credit else "completed",
            invoice_number=sale_data.invoice_number
        )
        
        db.add(sale)
        db.flush()

        # Création des items
        for item in sale_data.items:
            product = db.query(Product).filter(
                Product.id == item.product_id,
                Product.pharmacy_id == pharmacy.id
            ).first()
            
            sale_item = SaleItem(
                sale_id=sale.id,
                tenant_id=current_tenant.id,
                pharmacy_id=pharmacy.id,
                product_id=item.product_id,
                product_code=getattr(product, 'code', f"PRD-{product.id[:6]}"),
                product_name=getattr(product, 'name', 'Unknown'),
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_percent=item.discount_percent or Decimal('0.00'),
                tva_rate=item.tva_rate or getattr(product, 'tva_rate', Decimal('0.00')),
                batch_number=item.batch_number,
                expiry_date=item.expiry_date
            )
            sale_item.calculate_totals()
            db.add(sale_item)
            
            # Mise à jour stock
            try:
                inventory_service.update_stock(
                    product_id=item.product_id,
                    pharmacy_id=pharmacy.id,
                    quantity_change=-item.quantity,
                    reason="vente",
                    reference=sale.reference,
                    batch_number=item.batch_number,
                    cost_price=getattr(product, 'purchase_price', Decimal('0.00')),
                    selling_price=item.unit_price
                )
            except Exception as e:
                logger.error(f"Erreur mise à jour stock: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Erreur mise à jour stock: {str(e)}"
                )

        # Mise à jour client
        if client:
            client.total_achats = (getattr(client, 'total_achats', 0) or 0) + sale_data.total_amount
            client.nombre_achats = (getattr(client, 'nombre_achats', 0) or 0) + 1
            client.dernier_achat = datetime.utcnow()
            client.dernier_montant = sale_data.total_amount
            
            if sale_data.is_credit:
                client.dette_actuelle = (getattr(client, 'dette_actuelle', 0) or 0) + sale_data.total_amount

        # Validation automatique si configuré
        if settings.AUTO_VALIDATE_SALES and current_user.role in ["admin", "gerant"]:
            sale.status = "completed"
            sale.validated_by = current_user.id
            sale.validated_at = datetime.utcnow()

        db.commit()
        db.refresh(sale)

        # Tâches en arrière-plan
        background_tasks.add_task(generate_sale_receipt, sale.id, current_tenant.id, pharmacy.id, db)
        background_tasks.add_task(send_sale_notification, sale.id, current_tenant.id, pharmacy.id, db)
        background_tasks.add_task(update_sales_statistics, current_tenant.id, pharmacy.id, datetime.now().date(), db)

        # Préparation de la réponse
        sale_dict = sale.to_dict() if hasattr(sale, 'to_dict') else {}
        sale_in_db = SaleInDB(**sale_dict, pharmacy_name=pharmacy.name, pharmacy_code=pharmacy.pharmacy_code)
        
        return SaleResponse(
            message="Vente créée avec succès",
            sale=sale_in_db,
            pharmacy={
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "code": pharmacy.pharmacy_code
            },
            receipt_available=True,
            receipt_url=f"/api/v1/sales/{sale.id}/receipt" if hasattr(sale, 'receipt_path') and sale.receipt_path else None
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur création vente: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur création vente: {str(e)}"
        )

@router.get("/", response_model=SaleListResponse)
@require_permission("sales:read")
async def list_sales(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    pharmacy_id: Optional[UUID] = Query(None, description="Filtrer par pharmacie"),
    status: Optional[str] = Query(None, description="Statut de la vente"),
    payment_method: Optional[str] = Query(None, description="Méthode de paiement"),
    is_credit: Optional[bool] = Query(None, description="Ventes à crédit uniquement"),
    start_date: Optional[date] = Query(None, description="Date de début"),
    end_date: Optional[date] = Query(None, description="Date de fin"),
    client_id: Optional[UUID] = Query(None, description="ID du client"),
    seller_id: Optional[UUID] = Query(None, description="ID du vendeur"),
    search: Optional[str] = Query(None, description="Recherche texte"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc")
):
    """
    Liste les ventes avec filtres avancés par pharmacie
    """
    try:
        # Déterminer les pharmacies accessibles
        if current_user.role == "admin":
            accessible_pharmacies = db.query(Pharmacy.id).filter(
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active == True
            ).all()
            pharmacy_ids = [p.id for p in accessible_pharmacies]
        else:
            pharmacy_ids = get_user_accessible_pharmacies(db, current_user.id, current_tenant.id)
        
        if not pharmacy_ids:
            return SaleListResponse(items=[], total=0, page=1, size=limit, has_more=False)
        
        # Construction de la requête
        query = db.query(Sale).options(
            joinedload(Sale.pharmacy)
        ).filter(
            Sale.tenant_id == current_tenant.id,
            Sale.pharmacy_id.in_(pharmacy_ids)
        )
        
        # Filtres
        if pharmacy_id:
            if pharmacy_id not in pharmacy_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            query = query.filter(Sale.pharmacy_id == pharmacy_id)
        
        if status:
            query = query.filter(Sale.status == status)
        
        if payment_method:
            query = query.filter(Sale.payment_method == payment_method)
        
        if is_credit is not None:
            query = query.filter(Sale.is_credit == is_credit)
        
        if start_date:
            query = query.filter(func.date(Sale.created_at) >= start_date)
        
        if end_date:
            query = query.filter(func.date(Sale.created_at) <= end_date)
        
        if client_id:
            query = query.filter(Sale.client_id == client_id)
        
        if seller_id:
            query = query.filter(Sale.created_by == seller_id)
        
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                or_(
                    Sale.reference.ilike(search_term),
                    Sale.client_name.ilike(search_term),
                    Sale.invoice_number.ilike(search_term)
                )
            )
        
        # Tri
        sort_column = getattr(Sale, sort_by, Sale.created_at)
        if sort_order == "desc":
            query = query.order_by(desc(sort_column))
        else:
            query = query.order_by(sort_column)
        
        # Pagination
        total = query.count()
        sales = query.offset(skip).limit(limit).all()
        
        # Conversion en SaleInDB
        sales_in_db = []
        for sale in sales:
            sale_dict = sale.to_dict() if hasattr(sale, 'to_dict') else {}
            pharmacy = sale.pharmacy
            sales_in_db.append(SaleInDB(
                **sale_dict,
                pharmacy_name=pharmacy.name if pharmacy else None,
                pharmacy_code=pharmacy.pharmacy_code if pharmacy else None
            ))
        
        # Calcul des statistiques par pharmacie
        pharmacies_summary = {}
        if pharmacy_id:
            # Statistiques pour la pharmacie spécifique
            stats = db.query(
                func.count(Sale.id).label('count'),
                func.sum(Sale.total_amount).label('total')
            ).filter(
                Sale.tenant_id == current_tenant.id,
                Sale.pharmacy_id == pharmacy_id,
                Sale.status == 'completed'
            ).first()
            
            pharmacies_summary = {
                "pharmacy_id": str(pharmacy_id),
                "total_sales": stats.count or 0,
                "total_amount": float(stats.total or 0)
            }
        
        return SaleListResponse(
            items=sales_in_db,
            total=total,
            page=skip // limit + 1 if limit > 0 else 1,
            size=limit,
            has_more=skip + limit < total,
            pharmacies_summary=pharmacies_summary
        )
        
    except Exception as e:
        logger.error(f"Erreur listing ventes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur listing ventes: {str(e)}"
        )

@router.get("/{sale_id}", response_model=SaleDetailResponse)
@require_permission("sales:read")
async def get_sale_detail(
    sale_id: UUID,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère les détails d'une vente spécifique
    """
    try:
        sale = db.query(Sale).options(
            selectinload(Sale.items),
            selectinload(Sale.pharmacy),
            selectinload(Sale.client),
            selectinload(Sale.creator),
            selectinload(Sale.payments),
            selectinload(Sale.refunds)
        ).filter(
            Sale.id == sale_id,
            Sale.tenant_id == current_tenant.id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        # Vérifier l'accès à la pharmacie
        if current_user.role != "admin":
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, current_tenant.id)
            if sale.pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette vente"
                )
        
        # Conversion des items
        items_response = []
        for item in sale.items:
            items_response.append(SaleItemResponse(
                id=item.id,
                sale_id=item.sale_id,
                tenant_id=item.tenant_id,
                pharmacy_id=item.pharmacy_id,
                product_id=item.product_id,
                product_code=item.product_code,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount_percent=item.discount_percent,
                tva_rate=item.tva_rate,
                batch_number=item.batch_number,
                expiry_date=item.expiry_date,
                subtotal=item.subtotal,
                discount_amount=item.discount_amount,
                tva_amount=item.tva_amount,
                total=item.total,
                created_at=item.created_at
            ))
        
        # Préparation des données
        sale_dict = sale.to_dict() if hasattr(sale, 'to_dict') else {}
        pharmacy = sale.pharmacy
        client = sale.client
        creator = sale.creator
        
        sale_in_db = SaleInDB(
            **sale_dict,
            pharmacy_name=pharmacy.name if pharmacy else None,
            pharmacy_code=pharmacy.pharmacy_code if pharmacy else None
        )
        
        can_refund = sale.status == "completed" and not sale.is_credit
        can_cancel = sale.status in ["draft", "pending"]
        can_validate = sale.status == "pending" and current_user.role in ["admin", "gerant"]
        
        return SaleDetailResponse(
            sale=sale_in_db,
            items=items_response,
            pharmacy={
                "id": str(pharmacy.id) if pharmacy else None,
                "name": pharmacy.name if pharmacy else None,
                "code": pharmacy.pharmacy_code if pharmacy else None,
                "address": pharmacy.address if pharmacy else None
            },
            client={
                "id": str(client.id) if client else None,
                "name": client.nom_complet if client else None,
                "phone": client.telephone if client else None
            } if client else None,
            creator={
                "id": str(creator.id) if creator else None,
                "name": creator.nom_complet if creator else None,
                "role": creator.role if creator else None
            },
            payments=[],  # À implémenter selon ton modèle
            refunds=[],   # À implémenter selon ton modèle
            can_refund=can_refund,
            can_cancel=can_cancel,
            can_validate=can_validate
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erreur récupération vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur récupération vente: {str(e)}"
        )

@router.get("/stats/daily", response_model=Dict[str, Any])
@require_permission("sales:stats")
async def get_daily_stats(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user),
    date_param: Optional[date] = Query(None, description="Date spécifique (défaut: aujourd'hui)"),
    pharmacy_id: Optional[UUID] = Query(None, description="Pharmacie spécifique")
):
    """
    Statistiques quotidiennes des ventes par pharmacie
    """
    try:
        target_date = date_param or date.today()
        
        # Déterminer les pharmacies
        if current_user.role == "admin":
            if pharmacy_id:
                pharmacies = [pharmacy_id]
            else:
                pharmacies = db.query(Pharmacy.id).filter(
                    Pharmacy.tenant_id == current_tenant.id,
                    Pharmacy.is_active == True
                ).all()
                pharmacies = [p.id for p in pharmacies]
        else:
            accessible_pharmacies = get_user_accessible_pharmacies(db, current_user.id, current_tenant.id)
            if pharmacy_id and pharmacy_id not in accessible_pharmacies:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès non autorisé à cette pharmacie"
                )
            pharmacies = [pharmacy_id] if pharmacy_id else accessible_pharmacies
        
        if not pharmacies:
            return {
                "date": target_date.isoformat(),
                "sales_count": 0,
                "total_amount": 0.0,
                "average_basket": 0.0,
                "items_sold": 0,
                "top_products": [],
                "by_pharmacy": []
            }
        
        # Statistiques globales
        sales_today = db.query(Sale).filter(
            Sale.tenant_id == current_tenant.id,
            func.date(Sale.created_at) == target_date,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies)
        ).all()
        
        total_amount = sum(sale.total_amount for sale in sales_today)
        sales_count = len(sales_today)
        average_basket = total_amount / sales_count if sales_count > 0 else Decimal('0.00')
        
        # Produits vendus
        items_sold = db.query(func.sum(SaleItem.quantity)).join(Sale).filter(
            Sale.tenant_id == current_tenant.id,
            func.date(Sale.created_at) == target_date,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies)
        ).scalar() or 0
        
        # Top produits
        top_products = db.query(
            SaleItem.product_name,
            func.sum(SaleItem.quantity).label("total_quantity"),
            func.sum(SaleItem.total).label("total_amount")
        ).join(Sale).filter(
            Sale.tenant_id == current_tenant.id,
            func.date(Sale.created_at) == target_date,
            Sale.status == "completed",
            Sale.pharmacy_id.in_(pharmacies)
        ).group_by(SaleItem.product_name).order_by(desc("total_quantity")).limit(5).all()
        
        # Statistiques par pharmacie
        by_pharmacy = []
        for pharmacy in pharmacies:
            pharmacy_sales = db.query(Sale).filter(
                Sale.tenant_id == current_tenant.id,
                func.date(Sale.created_at) == target_date,
                Sale.status == "completed",
                Sale.pharmacy_id == pharmacy
            ).all()
            
            if pharmacy_sales:
                ph = db.query(Pharmacy).filter(Pharmacy.id == pharmacy).first()
                ph_total = sum(s.total_amount for s in pharmacy_sales)
                ph_count = len(pharmacy_sales)
                
                by_pharmacy.append({
                    "pharmacy_id": str(pharmacy),
                    "pharmacy_name": ph.name if ph else "Unknown",
                    "sales_count": ph_count,
                    "total_amount": float(ph_total),
                    "percentage": float((ph_total / total_amount * 100) if total_amount > 0 else 0)
                })
        
        return {
            "date": target_date.isoformat(),
            "sales_count": sales_count,
            "total_amount": float(total_amount),
            "average_basket": float(average_basket),
            "items_sold": items_sold,
            "top_products": [
                {
                    "product": product,
                    "quantity": int(quantity),
                    "amount": float(amount)
                }
                for product, quantity, amount in top_products
            ],
            "by_pharmacy": by_pharmacy
        }
        
    except Exception as e:
        logger.error(f"Erreur stats quotidiennes: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur stats quotidiennes: {str(e)}"
        )

@router.post("/{sale_id}/validate", response_model=SaleResponse)
@require_role(["admin", "gerant"])
async def validate_sale(
    sale_id: UUID,
    validation_data: SaleValidationRequest,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Valide une vente (pour les ventes à crédit ou nécessitant validation)
    """
    try:
        sale = db.query(Sale).filter(
            Sale.id == sale_id,
            Sale.tenant_id == current_tenant.id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        if sale.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La vente n'est pas en attente de validation (statut: {sale.status})"
            )
        
        # Validation
        sale.status = "completed"
        sale.validated_by = current_user.id
        sale.validated_at = datetime.utcnow()
        
        if validation_data.validator_notes:
            sale.notes = f"{sale.notes or ''}\nValidation: {validation_data.validator_notes}"
        
        db.commit()
        
        logger.info(f"Vente validée: {sale.reference} par {current_user.nom_complet}")
        
        # Conversion en SaleInDB
        sale_dict = sale.to_dict() if hasattr(sale, 'to_dict') else {}
        sale_in_db = SaleInDB(**sale_dict)
        
        return SaleResponse(
            message="Vente validée avec succès",
            sale=sale_in_db
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur validation vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur validation vente: {str(e)}"
        )

@router.post("/{sale_id}/cancel", response_model=SaleResponse)
@require_permission("sales:cancel")
async def cancel_sale(
    sale_id: UUID,
    cancel_reason: str = Query(..., description="Raison de l'annulation"),
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Annule une vente et restaure les stocks
    """
    try:
        sale = db.query(Sale).options(selectinload(Sale.items)).filter(
            Sale.id == sale_id,
            Sale.tenant_id == current_tenant.id
        ).first()
        
        if not sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Vente non trouvée"
            )
        
        if sale.status in ["cancelled", "refunded"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La vente est déjà {sale.status}"
            )
        
        # Vérifier les permissions
        can_cancel = (
            current_user.role in ["admin", "gerant"] or
            (current_user.id == sale.created_by and 
             (datetime.utcnow() - sale.created_at).total_seconds() < 3600)
        )
        
        if not can_cancel:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'êtes pas autorisé à annuler cette vente"
            )
        
        # Restaurer les stocks
        inventory_service = InventoryService(db, current_tenant.id)
        for item in sale.items:
            inventory_service.update_stock(
                product_id=item.product_id,
                pharmacy_id=sale.pharmacy_id,
                quantity_change=item.quantity,
                reason="annulation_vente",
                reference=f"ANN-{sale.reference}"
            )
        
        # Mise à jour client si crédit
        if sale.client_id and sale.is_credit:
            client = db.query(Client).filter(
                Client.id == sale.client_id,
                Client.tenant_id == current_tenant.id
            ).first()
            if client and hasattr(client, 'dette_actuelle'):
                client.dette_actuelle -= sale.total_amount - (sale.guarantee_deposit or Decimal('0.00'))
        
        # Annulation
        sale.status = "cancelled"
        sale.cancelled_by = current_user.id
        sale.cancelled_at = datetime.utcnow()
        sale.cancel_reason = f"{cancel_reason} (par {current_user.nom_complet})"
        
        db.commit()
        
        logger.warning(f"Vente annulée: {sale.reference} - Raison: {cancel_reason}")
        
        # Conversion en SaleInDB
        sale_dict = sale.to_dict() if hasattr(sale, 'to_dict') else {}
        sale_in_db = SaleInDB(**sale_dict)
        
        return SaleResponse(
            message="Vente annulée avec succès",
            sale=sale_in_db
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur annulation vente {sale_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur annulation vente: {str(e)}"
        )

@router.get("/pharmacy/context", response_model=UserPharmacyAccess)
async def get_pharmacy_context(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_active_user)
):
    """
    Récupère le contexte des pharmacies accessibles pour l'utilisateur
    """
    try:
        # Pharmacies accessibles
        accessible_pharmacies = []
        if current_user.role == "admin":
            pharmacies = db.query(Pharmacy).filter(
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active == True
            ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()
        else:
            user_pharmacies = db.query(UserPharmacy).filter(
                UserPharmacy.user_id == current_user.id
            ).all()
            
            pharmacy_ids = [up.pharmacy_id for up in user_pharmacies]
            pharmacies = db.query(Pharmacy).filter(
                Pharmacy.id.in_(pharmacy_ids),
                Pharmacy.tenant_id == current_tenant.id,
                Pharmacy.is_active == True
            ).order_by(Pharmacy.is_main.desc(), Pharmacy.name).all()
        
        for pharmacy in pharmacies:
            accessible_pharmacies.append({
                "id": pharmacy.id,
                "name": pharmacy.name,
                "code": pharmacy.pharmacy_code,
                "address": pharmacy.address,
                "phone": pharmacy.phone,
                "is_main": pharmacy.is_main,
                "is_active": pharmacy.is_active
            })
        
        # Pharmacie courante
        current_pharmacy = None
        user_pharmacy = db.query(UserPharmacy).filter(
            UserPharmacy.user_id == current_user.id,
            UserPharmacy.is_primary == True
        ).first()
        
        if user_pharmacy:
            pharmacy = db.query(Pharmacy).filter(Pharmacy.id == user_pharmacy.pharmacy_id).first()
            if pharmacy:
                current_pharmacy = {
                    "id": pharmacy.id,
                    "name": pharmacy.name,
                    "code": pharmacy.pharmacy_code,
                    "address": pharmacy.address,
                    "phone": pharmacy.phone,
                    "is_main": pharmacy.is_main,
                    "is_active": pharmacy.is_active
                }
        
        return UserPharmacyAccess(
            accessible_pharmacies=accessible_pharmacies,
            current_pharmacy=current_pharmacy,
            can_switch=len(accessible_pharmacies) > 1
        )
        
    except Exception as e:
        logger.error(f"Erreur contexte pharmacie: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur contexte pharmacie: {str(e)}"
        )

@router.post("/pharmacy/switch/{pharmacy_id}")
async def switch_active_pharmacy(
    pharmacy_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Change la pharmacie active pour l'utilisateur
    """
    try:
        # Vérifier l'accès
        user_pharmacy = db.query(UserPharmacy).filter(
            UserPharmacy.user_id == current_user.id,
            UserPharmacy.pharmacy_id == pharmacy_id
        ).first()
        
        if not user_pharmacy:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette pharmacie"
            )
        
        # Réinitialiser toutes les pharmacies principales
        db.query(UserPharmacy).filter(
            UserPharmacy.user_id == current_user.id
        ).update({"is_primary": False})
        
        # Définir la nouvelle pharmacie principale
        user_pharmacy.is_primary = True
        user_pharmacy.updated_at = datetime.utcnow()
        
        db.commit()
        
        # Récupérer la pharmacie
        pharmacy = db.query(Pharmacy).filter(Pharmacy.id == pharmacy_id).first()
        
        return {
            "message": f"Pharmacie active changée vers {pharmacy.name}",
            "pharmacy": {
                "id": str(pharmacy.id),
                "name": pharmacy.name,
                "code": pharmacy.pharmacy_code,
                "is_main": pharmacy.is_main,
                "address": pharmacy.address
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Erreur changement pharmacie: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erreur changement pharmacie: {str(e)}"
        )

# =======================
# Route de test
# =======================
@router.get("/test", include_in_schema=False)
async def test_sales():
    """
    Endpoint de test
    """
    return {
        "message": "Module Ventes avec Pharmacies opérationnel",
        "version": "3.0.0",
        "features": [
            "Gestion complète des ventes par pharmacie",
            "Multi-pharmacies pour les admin",
            "Contrôle d'accès par pharmacie",
            "Statistiques par pharmacie",
            "Transferts entre pharmacies",
            "Gestion des stocks par pharmacie"
        ],
        "timestamp": datetime.utcnow().isoformat()
    }