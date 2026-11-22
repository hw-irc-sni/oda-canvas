"""Initial schema: resources, documents and blobs, TMF701 processes and tasks, governance, hubs, feedback.

Revision ID: 0001_initial
Revises: 
Create Date: 2026-11-22 15:08:36.892639
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('hub_subscription',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('callback', sa.Text(), nullable=False),
    sa.Column('query', sa.Text(), nullable=True),
    sa.Column('owner', sa.Text(), nullable=True),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('knowledge',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('lifecycle_status', sa.String(length=32), nullable=False),
    sa.Column('store_kind', sa.String(length=16), nullable=False),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_knowledge_lifecycle_status'), 'knowledge', ['lifecycle_status'], unique=False)
    op.create_table('knowledge_specification',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('iri', sa.Text(), nullable=False),
    sa.Column('spec_kind', sa.String(length=16), nullable=False),
    sa.Column('layer', sa.String(length=16), nullable=True),
    sa.Column('lifecycle_status', sa.String(length=32), nullable=False),
    sa.Column('link_status', sa.String(length=16), nullable=True),
    sa.Column('body', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('iri')
    )
    op.create_index(op.f('ix_knowledge_specification_lifecycle_status'), 'knowledge_specification', ['lifecycle_status'], unique=False)
    op.create_table('document',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('knowledge_id', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('media_type', sa.Text(), nullable=False),
    sa.Column('size', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('ingestion_state', sa.String(length=16), nullable=False),
    sa.Column('last_process_id', sa.Text(), nullable=True),
    sa.Column('upload_date', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_id'], ['knowledge.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_document_knowledge_id'), 'document', ['knowledge_id'], unique=False)
    op.create_table('mapping',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('specification_id', sa.Text(), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('predicate', sa.Text(), nullable=False),
    sa.Column('object', sa.Text(), nullable=False),
    sa.Column('confidence', sa.String(length=8), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('lifecycle_status', sa.String(length=32), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['specification_id'], ['knowledge_specification.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_mapping_specification_id'), 'mapping', ['specification_id'], unique=False)
    op.create_table('process',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('knowledge_id', sa.Text(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['knowledge_id'], ['knowledge.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_process_knowledge_id'), 'process', ['knowledge_id'], unique=False)
    op.create_table('recommend_feedback',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('knowledge_id', sa.Text(), nullable=False),
    sa.Column('recommendation_id', sa.Text(), nullable=False),
    sa.Column('item_id', sa.Text(), nullable=False),
    sa.Column('used', sa.Boolean(), nullable=True),
    sa.Column('outcome', sa.String(length=16), nullable=True),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['knowledge_id'], ['knowledge.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_recommend_feedback_knowledge_id'), 'recommend_feedback', ['knowledge_id'], unique=False)
    op.create_table('review_item',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('specification_id', sa.Text(), nullable=False),
    sa.Column('target_kind', sa.String(length=16), nullable=False),
    sa.Column('target_id', sa.Text(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('text', sa.Text(), nullable=True),
    sa.Column('ruling', sa.String(length=16), nullable=True),
    sa.Column('link_status', sa.String(length=16), nullable=True),
    sa.Column('author', sa.Text(), nullable=False),
    sa.Column('approver', sa.Text(), nullable=True),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['specification_id'], ['knowledge_specification.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_review_item_specification_id'), 'review_item', ['specification_id'], unique=False)
    op.create_table('document_blob',
    sa.Column('document_id', sa.Text(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['document.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('document_id', 'seq')
    )
    op.create_table('state_transition',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('target_kind', sa.String(length=16), nullable=False),
    sa.Column('target_id', sa.Text(), nullable=False),
    sa.Column('from_state', sa.String(length=32), nullable=True),
    sa.Column('to_state', sa.String(length=32), nullable=False),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('review_item_id', sa.Text(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['review_item_id'], ['review_item.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_state_transition_target', 'state_transition', ['target_kind', 'target_id'], unique=False)
    op.create_table('task',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('process_id', sa.Text(), nullable=False),
    sa.Column('step', sa.String(length=16), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('locked_by', sa.Text(), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['process_id'], ['process.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_task_claim', 'task', ['state', 'created'], unique=False)
    op.create_index(op.f('ix_task_process_id'), 'task', ['process_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_process_id'), table_name='task')
    op.drop_index('ix_task_claim', table_name='task')
    op.drop_table('task')
    op.drop_index('ix_state_transition_target', table_name='state_transition')
    op.drop_table('state_transition')
    op.drop_table('document_blob')
    op.drop_index(op.f('ix_review_item_specification_id'), table_name='review_item')
    op.drop_table('review_item')
    op.drop_index(op.f('ix_recommend_feedback_knowledge_id'), table_name='recommend_feedback')
    op.drop_table('recommend_feedback')
    op.drop_index(op.f('ix_process_knowledge_id'), table_name='process')
    op.drop_table('process')
    op.drop_index(op.f('ix_mapping_specification_id'), table_name='mapping')
    op.drop_table('mapping')
    op.drop_index(op.f('ix_document_knowledge_id'), table_name='document')
    op.drop_table('document')
    op.drop_index(op.f('ix_knowledge_specification_lifecycle_status'), table_name='knowledge_specification')
    op.drop_table('knowledge_specification')
    op.drop_index(op.f('ix_knowledge_lifecycle_status'), table_name='knowledge')
    op.drop_table('knowledge')
    op.drop_table('hub_subscription')
