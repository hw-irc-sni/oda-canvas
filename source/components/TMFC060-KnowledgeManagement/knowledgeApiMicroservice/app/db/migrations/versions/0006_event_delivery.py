"""The event outbox: deliveries of events to hub subscriptions, retried with backoff.

Revision ID: 0006_event_delivery
Revises: 0005_task_chain
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = '0006_event_delivery'
down_revision = '0005_task_chain'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'event_delivery',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('subscription_id', sa.Text(), sa.ForeignKey('hub_subscription.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('body', JSONB(), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_error', sa.Text()),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('delivered_at', sa.DateTime(timezone=True)),
    )
    op.create_index('ix_event_delivery_due', 'event_delivery', ['state', 'next_attempt_at'])
    op.create_index('ix_event_delivery_subscription_id', 'event_delivery', ['subscription_id'])


def downgrade() -> None:
    op.drop_table('event_delivery')
