"""Recommendations made, entity applicability profiles, and one feedback per recommendation, item and caller.

Revision ID: 0008_recommendation
Revises: 0007_knowledge_proposal
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0008_recommendation'
down_revision = '0007_knowledge_proposal'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recommendation',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('knowledge_id', sa.Text(), sa.ForeignKey('knowledge.id', ondelete='CASCADE'), nullable=False),
        sa.Column('items', JSONB(), nullable=False),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_recommendation_knowledge_id', 'recommendation', ['knowledge_id'])
    op.create_table(
        'recommend_profile',
        sa.Column('knowledge_id', sa.Text(), sa.ForeignKey('knowledge.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('entity_iri', sa.Text(), primary_key=True),
        sa.Column('iris', JSONB(), nullable=False),
        sa.Column('updated', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_unique_constraint('uq_recommend_feedback', 'recommend_feedback', ['recommendation_id', 'item_id', 'actor'])


def downgrade() -> None:
    op.drop_constraint('uq_recommend_feedback', 'recommend_feedback', type_='unique')
    op.drop_table('recommend_profile')
    op.drop_table('recommendation')
