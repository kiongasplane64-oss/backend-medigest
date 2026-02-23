# app/models/invoice.py
import uuid
from datetime import datetime, date, timedelta
from typing import Optional
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, Integer, Date, DECIMAL, Boolean, func
from sqlalchemy.dialects.postgresql import UUID, JSON
from sqlalchemy.orm import relationship, validates
from sqlalchemy.ext.hybrid import hybrid_property
from app.db.base import Base


class Invoice(Base):
    __tablename__ = "invoices"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Numéro unique avec format personnalisable
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    invoice_prefix = Column(String(10), default="INV")
    invoice_year = Column(Integer, default=lambda: datetime.utcnow().year)
    invoice_sequence = Column(Integer, nullable=False)
    
    # Type de facture
    invoice_type = Column(String(20), default="sale", comment="sale, subscription, proforma, credit_note")
    currency = Column(String(3), default="CDF", comment="Devise: CDF, USD, EUR")
    
    # Client (pour abonnements, le client est le tenant lui-même)
    client_id = Column(UUID(as_uuid=True), ForeignKey("clients.id"), nullable=True)
    client_name = Column(String(100), nullable=True)  # Peut être vide pour les abonnements
    client_email = Column(String(100), nullable=True)
    client_phone = Column(String(20), nullable=True)
    client_address = Column(Text, nullable=True)
    client_tax_id = Column(String(50), nullable=True)
    
    # Pour les factures d'abonnement
    subscription_plan = Column(String(50), nullable=True, comment="Plan d'abonnement facturé")
    subscription_period = Column(String(20), nullable=True, comment="monthly, yearly")
    subscription_start = Column(Date, nullable=True)
    subscription_end = Column(Date, nullable=True)
    
    # Vente associée (facultatif)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=True)
    
    # Créateur
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    
    # Dates
    issue_date = Column(Date, nullable=False, default=date.today)
    due_date = Column(Date, nullable=True)
    
    # Montants financiers
    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_tax = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total_discount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    shipping_amount = Column(DECIMAL(15, 2), default=0.0)
    total_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    amount_paid = Column(DECIMAL(15, 2), default=0.0)
    
    # Taxe
    tax_details = Column(JSON, default=dict)
    tax_exempt = Column(Boolean, default=False)
    
    # Statut
    status = Column(String(20), default="draft", 
                   comment="draft, issued, sent, partially_paid, paid, overdue, cancelled, refunded")
    
    # Paiement
    payment_status = Column(String(20), default="pending", 
                           comment="pending, partially_paid, paid, overdue, failed, refunded")
    payment_methods = Column(JSON, default=list, comment="Liste des méthodes de paiement acceptées")
    
    # Paiement en ligne (si applicable)
    payment_gateway = Column(String(50), nullable=True)
    payment_gateway_id = Column(String(100), nullable=True)
    payment_gateway_status = Column(String(50), nullable=True)
    
    # Métadonnées et fichiers
    notes = Column(Text, nullable=True)
    terms = Column(Text, nullable=True)
    footer = Column(Text, nullable=True)
    invoice_meta = Column(JSON, default=dict)
    
    # Fichiers
    pdf_path = Column(String(500), nullable=True)
    xml_path = Column(String(500), nullable=True)
    receipt_path = Column(String(500), nullable=True)
    
    # Communication
    email_sent = Column(Boolean, default=False)
    email_sent_at = Column(DateTime, nullable=True)
    email_recipients = Column(JSON, default=list)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    archived_at = Column(DateTime, nullable=True)
    
    # Flags
    is_recurring = Column(Boolean, default=False)
    recurring_interval = Column(String(20), nullable=True, comment="monthly, quarterly, yearly")
    next_invoice_date = Column(Date, nullable=True)
    parent_invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True)
    is_credit_note = Column(Boolean, default=False)
    credit_note_for = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=True)
    
    # =====================================
    # RELATIONS
    # =====================================
    tenant = relationship("Tenant")
    client = relationship("Client")
    sale = relationship("Sale")
    creator = relationship("User", foreign_keys=[created_by])
    items = relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan")
    payments = relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan")
    
    # Relations récursives
    parent = relationship("Invoice", remote_side=[id], foreign_keys=[parent_invoice_id])
    credit_notes = relationship("Invoice", foreign_keys=[credit_note_for])
    
    # =====================================
    # VALIDATEURS
    # =====================================
    @validates('total_amount', 'subtotal', 'total_tax', 'total_discount')
    def validate_amounts(self, key, value):
        """Valide que les montants ne sont pas négatifs"""
        if value is not None and float(value) < 0:
            raise ValueError(f"{key} ne peut pas être négatif")
        return value
    
    @validates('due_date')
    def validate_due_date(self, key, value):
        """Valide que la date d'échéance n'est pas antérieure à la date d'émission"""
        if value and self.issue_date and value < self.issue_date:
            raise ValueError("La date d'échéance ne peut pas être antérieure à la date d'émission")
        return value
    
    # =====================================
    # PROPRIÉTÉS HYBRIDES
    # =====================================
    @hybrid_property
    def amount_due(self):
        """Montant restant à payer"""
        return float(self.total_amount - self.amount_paid)
    
    @amount_due.expression
    def amount_due(cls):
        return cls.total_amount - cls.amount_paid
    
    @hybrid_property
    def is_overdue(self):
        """Vérifie si la facture est en retard"""
        if self.due_date and self.payment_status != 'paid':
            return date.today() > self.due_date
        return False
    
    @hybrid_property
    def is_partially_paid(self):
        """Vérifie si la facture est partiellement payée"""
        return self.amount_paid > 0 and self.amount_due > 0
    
    @hybrid_property
    def payment_progress(self):
        """Progression du paiement en pourcentage"""
        if self.total_amount > 0:
            return (float(self.amount_paid) / float(self.total_amount)) * 100
        return 100
    
    @hybrid_property
    def days_overdue(self):
        """Nombre de jours de retard"""
        if self.is_overdue and self.due_date:
            return (date.today() - self.due_date).days
        return 0
    
    @hybrid_property
    def is_subscription_invoice(self):
        """Vérifie si c'est une facture d'abonnement"""
        return self.invoice_type == 'subscription'
    
    @hybrid_property
    def has_pending_payments(self):
        """Vérifie s'il y a des paiements en attente"""
        if not self.payments:
            return False
        return any(p.is_pending for p in self.payments)
    
    # =====================================
    # MÉTHODES
    # =====================================
    def calculate_totals(self):
        """Recalcule tous les totaux à partir des items"""
        if not self.items:
            return
        
        subtotal = sum(float(item.subtotal) for item in self.items)
        total_tax = sum(float(item.tax_amount) for item in self.items)
        total_discount = sum(float(item.subtotal * (item.discount_percent / 100)) 
                           for item in self.items if item.discount_percent > 0)
        
        self.subtotal = subtotal
        self.total_tax = total_tax
        self.total_discount = total_discount
        self.total_amount = subtotal + total_tax + float(self.shipping_amount) - total_discount
    
    def add_payment(self, amount, payment_method, reference=None, notes=None, 
                   payment_gateway=None, transaction_id=None):
        """
        Ajoute un paiement à la facture
        """
        from .invoice_payment import InvoicePayment
        
        payment = InvoicePayment(
            invoice_id=self.id,
            tenant_id=self.tenant_id,
            amount=amount,
            payment_method=payment_method,
            reference=reference,
            notes=notes,
            payment_gateway=payment_gateway,
            transaction_id=transaction_id
        )
        
        self.amount_paid += amount
        
        # Mettre à jour le statut de paiement
        if float(self.amount_paid) >= float(self.total_amount):
            self.payment_status = 'paid'
            self.paid_at = datetime.utcnow()
            self.status = 'paid'
        elif float(self.amount_paid) > 0:
            self.payment_status = 'partially_paid'
        
        return payment
    
    def add_payment_from_general(self, general_payment, **kwargs):
        """
        Ajoute un paiement à partir d'un Payment général
        """
        from .invoice_payment import InvoicePayment
        
        invoice_payment = InvoicePayment(
            invoice_id=self.id,
            tenant_id=self.tenant_id,
            payment_id=general_payment.id,
            amount=general_payment.amount,
            payment_method=general_payment.payment_method,
            reference=general_payment.reference,
            transaction_id=general_payment.transaction_id,
            status=general_payment.status,
            payment_date=general_payment.paid_at or datetime.utcnow(),
            **kwargs
        )
        
        self.payments.append(invoice_payment)
        
        self.amount_paid += general_payment.amount
        
        # Mettre à jour le statut
        if float(self.amount_paid) >= float(self.total_amount):
            self.payment_status = 'paid'
            self.paid_at = general_payment.paid_at or datetime.utcnow()
            self.status = 'paid'
        elif float(self.amount_paid) > 0:
            self.payment_status = 'partially_paid'
        
        return invoice_payment
    
    def mark_as_paid(self, payment_method='cash', reference=None):
        """Marque la facture comme payée complètement"""
        remaining = self.amount_due
        if remaining > 0:
            self.add_payment(remaining, payment_method, reference)
    
    def generate_invoice_number(self):
        """Génère un numéro de facture unique"""
        from app.db.session import SessionLocal
        
        db = SessionLocal()
        try:
            # Compter les factures de l'année
            from .invoice import Invoice
            count = db.query(func.count(Invoice.id)).filter(
                Invoice.tenant_id == self.tenant_id,
                Invoice.invoice_year == datetime.utcnow().year
            ).scalar() or 0
            
            self.invoice_year = datetime.utcnow().year
            self.invoice_sequence = count + 1
            self.invoice_number = f"{self.invoice_prefix}-{self.invoice_year}-{self.invoice_sequence:06d}"
        finally:
            db.close()
    
    def create_credit_note(self, reason="", items_to_refund=None):
        """Crée une note de crédit pour cette facture"""
        credit_note = Invoice(
            tenant_id=self.tenant_id,
            invoice_type='credit_note',
            client_id=self.client_id,
            client_name=self.client_name,
            client_email=self.client_email,
            client_address=self.client_address,
            created_by=self.created_by,
            issue_date=date.today(),
            credit_note_for=self.id,
            is_credit_note=True,
            notes=f"Note de crédit pour la facture {self.invoice_number}. Raison: {reason}"
        )
        
        # Copier les items à rembourser
        if items_to_refund:
            for item in items_to_refund:
                credit_note.items.append(InvoiceItem(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    tax_rate=item.tax_rate
                ))
        else:
            # Rembourser tout
            for item in self.items:
                credit_note.items.append(InvoiceItem(
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    tax_rate=item.tax_rate
                ))
        
        credit_note.calculate_totals()
        credit_note.generate_invoice_number()
        
        return credit_note
    
    def to_dict(self, include_items=True, include_payments=False):
        """Convertit la facture en dictionnaire"""
        data = {
            'id': str(self.id),
            'invoice_number': self.invoice_number,
            'invoice_type': self.invoice_type,
            'issue_date': self.issue_date.isoformat() if self.issue_date else None,
            'due_date': self.due_date.isoformat() if self.due_date else None,
            'client_name': self.client_name,
            'client_email': self.client_email,
            'client_phone': self.client_phone,
            'subtotal': float(self.subtotal),
            'total_tax': float(self.total_tax),
            'total_discount': float(self.total_discount),
            'shipping_amount': float(self.shipping_amount),
            'total_amount': float(self.total_amount),
            'amount_paid': float(self.amount_paid),
            'amount_due': self.amount_due,
            'status': self.status,
            'payment_status': self.payment_status,
            'currency': self.currency,
            'is_overdue': self.is_overdue,
            'days_overdue': self.days_overdue,
            'payment_progress': self.payment_progress,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'is_subscription_invoice': self.is_subscription_invoice,
            'subscription_plan': self.subscription_plan,
            'subscription_period': self.subscription_period,
            'subscription_start': self.subscription_start.isoformat() if self.subscription_start else None,
            'subscription_end': self.subscription_end.isoformat() if self.subscription_end else None,
            'is_recurring': self.is_recurring,
            'recurring_interval': self.recurring_interval,
            'next_invoice_date': self.next_invoice_date.isoformat() if self.next_invoice_date else None,
            'email_sent': self.email_sent,
            'pdf_path': self.pdf_path
        }
        
        if include_items:
            data['items'] = [item.to_dict() for item in self.items]
        
        if include_payments:
            data['payments'] = [payment.to_dict() for payment in self.payments]
        
        return data
    
    def create_subscription_invoice(self, plan_name, period, amount, start_date, end_date):
        """
        Crée une facture d'abonnement récurrente
        """
        self.invoice_type = 'subscription'
        self.subscription_plan = plan_name
        self.subscription_period = period
        self.subscription_start = start_date
        self.subscription_end = end_date
        self.is_recurring = True
        self.recurring_interval = period
        
        # Calculer la prochaine date de facturation
        if period == 'monthly':
            self.next_invoice_date = end_date + timedelta(days=1)
        elif period == 'yearly':
            self.next_invoice_date = end_date + timedelta(days=365)
        
        # Ajouter l'item d'abonnement
        self.items.append(InvoiceItem(
            description=f"Abonnement {plan_name} - {period}",
            item_type='subscription',
            quantity=1,
            unit_price=amount,
            tax_rate=0.0,
            affects_stock=False
        ))
        
        self.calculate_totals()
        return self


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id = Column(UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    
    # Description
    description = Column(String(500), nullable=False)
    product_id = Column(UUID(as_uuid=True), ForeignKey("products.id"), nullable=True)
    
    # Type d'item
    item_type = Column(String(20), default="product", comment="product, service, subscription, discount, tax, shipping")
    
    # Quantité et prix
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(DECIMAL(15, 2), nullable=False)
    
    # Unités
    unit_of_measure = Column(String(20), default="unit", comment="unit, box, pack, kg, liter, etc.")
    
    # Taxe et remise
    tax_rate = Column(DECIMAL(5, 2), default=0.0)
    discount_percent = Column(DECIMAL(5, 2), default=0.0)
    discount_amount = Column(DECIMAL(15, 2), default=0.0)
    
    # Calculs
    subtotal = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    tax_amount = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    total = Column(DECIMAL(15, 2), nullable=False, default=0.0)
    
    # Stock (si applicable)
    affects_stock = Column(Boolean, default=True)
    stock_adjustment_id = Column(UUID(as_uuid=True), nullable=True)
    
    # Métadonnées
    notes = Column(Text, nullable=True)
    item_meta = Column(JSON, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relations
    invoice = relationship("Invoice", back_populates="items")
    tenant = relationship("Tenant")
    product = relationship("Product")
    
    # =====================================
    # VALIDATEURS
    # =====================================
    @validates('quantity')
    def validate_quantity(self, key, value):
        if value <= 0:
            raise ValueError("La quantité doit être positive")
        return value
    
    @validates('unit_price')
    def validate_unit_price(self, key, value):
        if value is not None and float(value) < 0:
            raise ValueError("Le prix unitaire ne peut pas être négatif")
        return value
    
    # =====================================
    # HOOKS
    # =====================================
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calculate_totals()
    
    def calculate_totals(self):
        """Calcule les totaux de l'item"""
        qty = self.quantity or 0
        price = float(self.unit_price or 0)
        
        # Calcul du sous-total
        self.subtotal = qty * price
        
        # Application de la remise en pourcentage
        if self.discount_percent and self.discount_percent > 0:
            discount = self.subtotal * (float(self.discount_percent) / 100)
            self.discount_amount = discount
            self.subtotal -= discount
        
        # Application de la remise en montant fixe
        if self.discount_amount and self.discount_amount > 0:
            self.subtotal -= float(self.discount_amount)
        
        # Calcul de la taxe
        self.tax_amount = self.subtotal * (float(self.tax_rate) / 100)
        
        # Total
        self.total = self.subtotal + self.tax_amount
    
    def to_dict(self):
        """Convertit l'item en dictionnaire"""
        return {
            'id': str(self.id),
            'description': self.description,
            'item_type': self.item_type,
            'quantity': self.quantity,
            'unit_price': float(self.unit_price),
            'unit_of_measure': self.unit_of_measure,
            'tax_rate': float(self.tax_rate),
            'discount_percent': float(self.discount_percent),
            'discount_amount': float(self.discount_amount),
            'subtotal': float(self.subtotal),
            'tax_amount': float(self.tax_amount),
            'total': float(self.total),
            'product_id': str(self.product_id) if self.product_id else None,
            'affects_stock': self.affects_stock,
            'notes': self.notes
        }