"""create score_user_roles table

Revision ID: 0001
Revises:
Create Date: 2026-05-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "score_user_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_user_project"),
    )
    op.create_index(op.f("ix_score_user_roles_id"), "score_user_roles", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_score_user_roles_id"), table_name="score_user_roles")
    op.drop_table("score_user_roles")
