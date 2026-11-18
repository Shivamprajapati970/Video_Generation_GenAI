"""Initial database schema with users, sessions, video_projects, project_images, generation_jobs, and generated_assets

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-11-13 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0001_initial_schema'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Users table
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('is_superuser', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # 2. Sessions table
    op.create_table(
        'sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_token', sa.String(length=128), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sessions_id'), 'sessions', ['id'], unique=False)
    op.create_index(op.f('ix_sessions_session_token'), 'sessions', ['session_token'], unique=True)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)

    # 3. Video projects table
    op.create_table(
        'video_projects',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('occasion', sa.String(length=50), nullable=False),
        sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('aspect_ratio', sa.String(length=10), server_default='9:16', nullable=False),
        sa.Column('music_mood', sa.String(length=50), nullable=False),
        sa.Column('custom_music_prompt', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('scene_plan', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_video_projects_id'), 'video_projects', ['id'], unique=False)
    op.create_index(op.f('ix_video_projects_user_id'), 'video_projects', ['user_id'], unique=False)

    # 4. Project images table
    op.create_table(
        'project_images',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('order_index', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['video_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_project_images_id'), 'project_images', ['id'], unique=False)
    op.create_index(op.f('ix_project_images_project_id'), 'project_images', ['project_id'], unique=False)

    # 5. Generation jobs table
    op.create_table(
        'generation_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('stage', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('progress_percent', sa.Integer(), server_default='0', nullable=False),
        sa.Column('external_provider_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['video_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_generation_jobs_id'), 'generation_jobs', ['id'], unique=False)
    op.create_index(op.f('ix_generation_jobs_project_id'), 'generation_jobs', ['project_id'], unique=False)
    op.create_index(op.f('ix_generation_jobs_stage'), 'generation_jobs', ['stage'], unique=False)
    op.create_index(op.f('ix_generation_jobs_status'), 'generation_jobs', ['status'], unique=False)

    # 6. Generated assets table
    op.create_table(
        'generated_assets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('asset_type', sa.String(length=50), nullable=False),
        sa.Column('file_path', sa.String(length=512), nullable=False),
        sa.Column('provider_url', sa.Text(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('meta_data', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['video_projects.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_generated_assets_id'), 'generated_assets', ['id'], unique=False)
    op.create_index(op.f('ix_generated_assets_project_id'), 'generated_assets', ['project_id'], unique=False)
    op.create_index(op.f('ix_generated_assets_asset_type'), 'generated_assets', ['asset_type'], unique=False)


def downgrade() -> None:
    op.drop_table('generated_assets')
    op.drop_table('generation_jobs')
    op.drop_table('project_images')
    op.drop_table('video_projects')
    op.drop_table('sessions')
    op.drop_table('users')
