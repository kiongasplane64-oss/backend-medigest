# app/models/invoice_counter.py

from sqlalchemy import Column, Integer, Date, DateTime
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base
import uuid
from datetime import date, datetime

class InvoiceCounter(Base):
    __tablename__ = "invoice_counters"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    pharmacy_id = Column(UUID(as_uuid=True), nullable=False)
    date = Column(Date, nullable=False, default=date.today)
    current_number = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)