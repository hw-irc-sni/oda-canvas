"""Mapping ids are unique per bridge module: the primary key becomes (specification_id, id).

Revision ID: 0003_mapping_key
Revises: 0002_validation_report
"""
from alembic import op

revision = '0003_mapping_key'
down_revision = '0002_validation_report'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ix_mapping_specification_id', table_name='mapping')
    op.drop_constraint('mapping_pkey', 'mapping', type_='primary')
    op.create_primary_key('mapping_pkey', 'mapping', ['specification_id', 'id'])


def downgrade() -> None:
    op.drop_constraint('mapping_pkey', 'mapping', type_='primary')
    op.create_primary_key('mapping_pkey', 'mapping', ['id'])
    op.create_index('ix_mapping_specification_id', 'mapping', ['specification_id'], unique=False)
