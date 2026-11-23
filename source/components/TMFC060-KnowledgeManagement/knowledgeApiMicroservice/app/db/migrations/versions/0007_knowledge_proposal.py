"""Proposed changes to a knowledge-base (KM-FX-04): data conflicts, ontology terms, entity links.

Revision ID: 0007_knowledge_proposal
Revises: 0006_event_delivery
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0007_knowledge_proposal'
down_revision = '0006_event_delivery'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_proposal',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('knowledge_id', sa.Text(), sa.ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('source', sa.Text()),
        sa.Column('process_id', sa.Text()),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('details', JSONB(), nullable=False),
        sa.Column('suggestion', JSONB()),
        sa.Column('decided_by', sa.Text()),
        sa.Column('decided_at', sa.DateTime(timezone=True)),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_knowledge_proposal_knowledge_id', 'knowledge_proposal', ['knowledge_id'])


def downgrade() -> None:
    op.drop_table('knowledge_proposal')
