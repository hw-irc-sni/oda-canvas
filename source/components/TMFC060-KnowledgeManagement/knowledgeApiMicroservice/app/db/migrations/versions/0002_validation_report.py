"""The latest validation report of each ontology module.

Revision ID: 0002_validation_report
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002_validation_report'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('validation_report',
    sa.Column('specification_id', sa.Text(), nullable=False),
    sa.Column('report', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('run_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['specification_id'], ['knowledge_specification.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('specification_id')
    )


def downgrade() -> None:
    op.drop_table('validation_report')
