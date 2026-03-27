# app/schemas/customer.py

from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime

from app.schemas.order import OrderResponse


class CustomerBase(BaseModel):
    """Modèle de base pour un client"""
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    """Modèle pour la création d'un client"""
    pass


class CustomerUpdate(BaseModel):
    """Modèle pour la mise à jour d'un client"""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class CustomerResponse(CustomerBase):
    """Modèle de réponse pour un client"""
    id: str
    tenant_id: str
    created_at: datetime
    updated_at: datetime
    
    # Relations optionnelles
    orders: Optional[List[OrderResponse]] = None
    
    class Config:
        from_attributes = True


class CustomerWithOrdersResponse(CustomerResponse):
    """Modèle de réponse pour un client avec ses commandes"""
    orders: List[OrderResponse] = []
    total_orders: int = 0
    total_spent: float = 0.0
    average_order_value: float = 0.0