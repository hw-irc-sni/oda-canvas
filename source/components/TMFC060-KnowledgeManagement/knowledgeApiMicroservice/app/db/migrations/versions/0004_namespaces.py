"""The @prefix declarations of each module's content, to bind the prefixes the module lists.

Revision ID: 0004_namespaces
Revises: 0003_mapping_key
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0004_namespaces'
down_revision = '0003_mapping_key'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('knowledge_specification', sa.Column('namespaces', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('knowledge_specification', 'namespaces')
