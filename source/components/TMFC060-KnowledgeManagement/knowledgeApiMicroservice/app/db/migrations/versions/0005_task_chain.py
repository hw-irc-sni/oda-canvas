"""Tasks belong to a chain (a document or source batch) whose steps run in order, and carry a retry time.

Revision ID: 0005_task_chain
Revises: 0004_namespaces
"""
import sqlalchemy as sa
from alembic import op

revision = '0005_task_chain'
down_revision = '0004_namespaces'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('task', sa.Column('chain', sa.Text(), nullable=False, server_default=''))
    op.alter_column('task', 'chain', server_default=None)
    op.add_column('task', sa.Column('available_at', sa.DateTime(timezone=True), nullable=False,
                                    server_default=sa.func.now()))
    op.drop_index('ix_task_claim', table_name='task')
    op.create_index('ix_task_claim', 'task', ['state', 'available_at'], unique=False)
    op.create_index('ix_task_chain', 'task', ['process_id', 'chain'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_task_chain', table_name='task')
    op.drop_index('ix_task_claim', table_name='task')
    op.create_index('ix_task_claim', 'task', ['state', 'created'], unique=False)
    op.drop_column('task', 'available_at')
    op.drop_column('task', 'chain')
