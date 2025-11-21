"""add admin broadcast history table

Revision ID: admin_broadcasts_2025
Revises: grace_period_2025
Create Date: 2025-10-14 10:00:00

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "admin_broadcasts_2025"
down_revision = "grace_period_2025"
branch_labels = None
depends_on = None

broadcast_audience_type = sa.Enum(
    "all",
    "verified",
    "unverified",
    "plan",
    "custom",
    name="broadcastaudiencetype",
)

broadcast_status = sa.Enum(
    "pending",
    "sent",
    "failed",
    name="broadcaststatus",
)


def upgrade():
    op.create_table(
        "admin_broadcasts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("html_content", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("template_name", sa.String(length=255), nullable=True),
        sa.Column("audience_type", broadcast_audience_type, nullable=False),
    sa.Column("audience_filters", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    sa.Column("total_recipients", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("sent_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    sa.Column("status", broadcast_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("sent_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_broadcasts_created_at", "admin_broadcasts", ["created_at"], unique=False)
    op.create_index("ix_admin_broadcasts_status", "admin_broadcasts", ["status"], unique=False)


def downgrade():
    op.drop_index("ix_admin_broadcasts_status", table_name="admin_broadcasts")
    op.drop_index("ix_admin_broadcasts_created_at", table_name="admin_broadcasts")
    op.drop_table("admin_broadcasts")

    bind = op.get_bind()
    broadcast_status.drop(bind, checkfirst=True)
    broadcast_audience_type.drop(bind, checkfirst=True)
