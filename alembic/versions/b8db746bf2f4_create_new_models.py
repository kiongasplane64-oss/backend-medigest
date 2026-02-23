"""create new models

Revision ID: b8db746bf2f4
Revises: b74c569b73cf
Create Date: 2025-12-25 11:25:58.683993
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# =======================
# Revision identifiers
# =======================
revision: str = "b8db746bf2f4"
down_revision: Union[str, Sequence[str], None] = "b74c569b73cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# =======================
# ENUMS PostgreSQL (déclarés UNE SEULE FOIS)
# =======================
payment_status_enum = postgresql.ENUM(
    "PENDING",
    "COMPLETED",
    "FAILED",
    "REFUNDED",
    "PARTIALLY_REFUNDED",
    name="paymentstatus",
    create_type=False,
)

payment_method_enum = postgresql.ENUM(
    "CASH",
    "MOBILE_MONEY",
    "BANK_TRANSFER",
    "CARD",
    "OTHER",
    name="paymentmethod",
    create_type=False,
)


def upgrade() -> None:
    # =======================
    # PURCHASES
    # =======================
    op.create_table(
        "purchases",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("invoice_number", sa.String(100)),
        sa.Column("supplier_id", sa.UUID()),
        sa.Column("supplier_name", sa.String(200), nullable=False),
        sa.Column("supplier_invoice", sa.String(100)),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("delivery_date", sa.Date()),
        sa.Column("payment_due_date", sa.Date()),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            comment="draft, ordered, received, partial, completed, cancelled",
        ),
        sa.Column("payment_method", sa.String(50)),
        sa.Column(
            "payment_status",
            sa.String(20),
            nullable=False,
            comment="pending, partial, paid, overdue",
        ),
        sa.Column("subtotal", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("discount_amount", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("shipping_cost", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("tax_amount", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("total_amount", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("amount_paid", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("amount_due", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("payment_notes", sa.Text()),
        sa.Column("delivery_notes", sa.Text()),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("received_by", sa.UUID()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("ordered_at", sa.DateTime()),
        sa.Column("received_at", sa.DateTime()),
        sa.Column("paid_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["received_by"], ["users.id"]),
    )

    op.create_index(
        "ix_purchases_reference",
        "purchases",
        ["reference"],
        unique=True,
    )
    op.create_index(
        "ix_purchases_tenant_date",
        "purchases",
        ["tenant_id", "purchase_date"],
    )
    op.create_index(
        "ix_purchases_tenant_status",
        "purchases",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_purchases_tenant_payment",
        "purchases",
        ["tenant_id", "payment_status"],
    )

    # =======================
    # SUBSCRIPTION PAYMENTS
    # =======================
    op.create_table(
        "subscription_payments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("payment_code", sa.String(50), nullable=False),
        sa.Column("amount", sa.DECIMAL(10, 2), nullable=False),
        sa.Column("amount_paid", sa.DECIMAL(10, 2), nullable=False),
        sa.Column("status", payment_status_enum, nullable=False),
        sa.Column("payment_method", payment_method_enum, nullable=False),
        sa.Column("payment_reference", sa.String(100)),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("meta_data", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("paid_at", sa.DateTime()),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["subscriptions.id"],
        ),
    )

    op.create_index(
        "ix_subscription_payments_payment_code",
        "subscription_payments",
        ["payment_code"],
        unique=True,
    )
    op.create_index(
        "ix_subscription_payments_subscription_id",
        "subscription_payments",
        ["subscription_id"],
    )

    # =======================
    # PURCHASE ITEMS
    # =======================
    op.create_table(
        "purchase_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("purchase_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.UUID(), nullable=False),
        sa.Column("product_code", sa.String(50), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("quantity_ordered", sa.DECIMAL(15, 3), nullable=False),
        sa.Column("quantity_received", sa.DECIMAL(15, 3), nullable=False),
        sa.Column("quantity_pending", sa.DECIMAL(15, 3), nullable=False),
        sa.Column("unit_price", sa.DECIMAL(15, 4), nullable=False),
        sa.Column("discount_percent", sa.DECIMAL(5, 2), nullable=False),
        sa.Column("subtotal", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("discount_amount", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("tax_percent", sa.DECIMAL(5, 2), nullable=False),
        sa.Column("tax_amount", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("total", sa.DECIMAL(15, 2), nullable=False),
        sa.Column("batch_number", sa.String(100)),
        sa.Column("expiry_date", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("location", sa.String(100)),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["purchase_id"], ["purchases.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )


def downgrade() -> None:
    op.drop_table("purchase_items")
    op.drop_table("subscription_payments")
    op.drop_table("purchases")
