"""Hub registrations on TMF providers, and the buffer of events waiting to be ingested.

Revision ID: 0009_event_sources
Revises: 0008_recommendation
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0009_event_sources'
down_revision = '0008_recommendation'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'event_source',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('dependency', sa.Text(), nullable=False),
        sa.Column('provider_url', sa.Text(), nullable=False),
        sa.Column('hub_id', sa.Text()),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('query', sa.Text()),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        'event_buffer',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('knowledge_id', sa.Text(), sa.ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('resource', JSONB(), nullable=False),
        sa.Column('received', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_event_buffer_kb_kind', 'event_buffer', ['knowledge_id', 'kind', 'received'])


def downgrade() -> None:
    op.drop_table('event_buffer')
    op.drop_table('event_source')
