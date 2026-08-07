"""create service_actor_override_logs table (cmd_172)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_actor_override_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("service_client_id", sa.String(), nullable=True),
        sa.Column("acting_user_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_service_actor_override_logs_id"), "service_actor_override_logs", ["id"], unique=False)
    op.create_index(op.f("ix_service_actor_override_logs_created_at"), "service_actor_override_logs", ["created_at"], unique=False)
    op.create_index(op.f("ix_service_actor_override_logs_endpoint"), "service_actor_override_logs", ["endpoint"], unique=False)
    op.create_index(op.f("ix_service_actor_override_logs_acting_user_id"), "service_actor_override_logs", ["acting_user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_service_actor_override_logs_acting_user_id"), table_name="service_actor_override_logs")
    op.drop_index(op.f("ix_service_actor_override_logs_endpoint"), table_name="service_actor_override_logs")
    op.drop_index(op.f("ix_service_actor_override_logs_created_at"), table_name="service_actor_override_logs")
    op.drop_index(op.f("ix_service_actor_override_logs_id"), table_name="service_actor_override_logs")
    op.drop_table("service_actor_override_logs")
