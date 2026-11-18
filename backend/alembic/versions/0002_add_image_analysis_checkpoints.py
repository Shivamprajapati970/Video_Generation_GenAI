"""Add image_analysis_checkpoints table

Revision ID: 0002_image_checkpoints
Revises: 0001_initial_schema
Create Date: 2026-11-16 20:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0002_image_checkpoints'
down_revision = '0001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'image_analysis_checkpoints',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('image_id', sa.Uuid(), nullable=False),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='PENDING'),
        sa.Column('analysis_result', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['image_id'], ['project_images.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['video_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'image_id', name='uq_project_image_checkpoint')
    )
    op.create_index(op.f('ix_image_analysis_checkpoints_id'), 'image_analysis_checkpoints', ['id'], unique=False)
    op.create_index(op.f('ix_image_analysis_checkpoints_image_id'), 'image_analysis_checkpoints', ['image_id'], unique=False)
    op.create_index(op.f('ix_image_analysis_checkpoints_project_id'), 'image_analysis_checkpoints', ['project_id'], unique=False)
    op.create_index(op.f('ix_image_analysis_checkpoints_status'), 'image_analysis_checkpoints', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_image_analysis_checkpoints_status'), table_name='image_analysis_checkpoints')
    op.drop_index(op.f('ix_image_analysis_checkpoints_project_id'), table_name='image_analysis_checkpoints')
    op.drop_index(op.f('ix_image_analysis_checkpoints_image_id'), table_name='image_analysis_checkpoints')
    op.drop_index(op.f('ix_image_analysis_checkpoints_id'), table_name='image_analysis_checkpoints')
    op.drop_table('image_analysis_checkpoints')
