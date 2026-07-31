"""create task_threads table

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_task_threads_task_id"),
    )
    op.create_index(op.f("ix_task_threads_id"), "task_threads", ["id"], unique=False)
    op.create_index(op.f("ix_task_threads_task_id"), "task_threads", ["task_id"], unique=True)
    op.create_index(op.f("ix_task_threads_thread_id"), "task_threads", ["thread_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_task_threads_thread_id"), table_name="task_threads")
    op.drop_index(op.f("ix_task_threads_task_id"), table_name="task_threads")
    op.drop_index(op.f("ix_task_threads_id"), table_name="task_threads")
    op.drop_table("task_threads")
