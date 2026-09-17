"""create uploaded_assets table (cmd_252 Asset Upload 新設枠)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploaded_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shot_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("stored_filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_uploaded_assets_id"), "uploaded_assets", ["id"], unique=False)
    op.create_index(op.f("ix_uploaded_assets_shot_id"), "uploaded_assets", ["shot_id"], unique=False)
    op.create_index(op.f("ix_uploaded_assets_task_id"), "uploaded_assets", ["task_id"], unique=False)
    op.create_index(op.f("ix_uploaded_assets_project_id"), "uploaded_assets", ["project_id"], unique=False)
    op.create_index(op.f("ix_uploaded_assets_created_at"), "uploaded_assets", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_uploaded_assets_created_at"), table_name="uploaded_assets")
    op.drop_index(op.f("ix_uploaded_assets_project_id"), table_name="uploaded_assets")
    op.drop_index(op.f("ix_uploaded_assets_task_id"), table_name="uploaded_assets")
    op.drop_index(op.f("ix_uploaded_assets_shot_id"), table_name="uploaded_assets")
    op.drop_index(op.f("ix_uploaded_assets_id"), table_name="uploaded_assets")
    op.drop_table("uploaded_assets")
