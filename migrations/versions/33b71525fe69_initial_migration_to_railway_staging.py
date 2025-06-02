"""initial migration to railway-staging

Revision ID: 33b71525fe69
Revises: 61541b793c8d
Create Date: 2025-06-02 10:01:55.554205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33b71525fe69'
down_revision: Union[str, None] = '61541b793c8d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    #
    # ── 1) DROP ALL EXISTING ENUM TYPES ──────────────────────────────────────────
    #
    # We drop each ENUM type if it already exists, so that when we create tables
    # (with sa.Enum(...)), Alembic/SQLAlchemy will recreate them from scratch.
    #
    op.execute("DROP TYPE IF EXISTS summarydetail CASCADE;")
    op.execute("DROP TYPE IF EXISTS aifeedbacklevel CASCADE;")
    op.execute("DROP TYPE IF EXISTS role CASCADE;")
    op.execute("DROP TYPE IF EXISTS materialstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS subscriptionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS difficulty CASCADE;")
    op.execute("DROP TYPE IF EXISTS sessionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS transactionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS questiontype CASCADE;")

    #
    # ── 2) CREATE TABLES (ENUMs will be auto‐created here) ──────────────────────
    #
    # By omitting `create_type=False`, SQLAlchemy will emit a CREATE TYPE for each
    # sa.Enum(…, name='…') before building its table.
    #
    op.create_table(
        'plans',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('price_pence', sa.Integer(), nullable=False),
        sa.Column('monthly_upload_limit', sa.Integer(), nullable=False),
        sa.Column('pages_per_upload_limit', sa.Integer(), nullable=False),
        sa.Column('monthly_assessment_limit', sa.Integer(), nullable=False),
        sa.Column('questions_per_assessment', sa.Integer(), nullable=False),
        sa.Column('monthly_ask_question_limit', sa.Integer(), nullable=False),
        sa.Column(
            'summary_detail',
            sa.Enum(
                'limited_detail',
                'deep_insights',
                name='summarydetail'  # SQLAlchemy will auto‐CREATE this ENUM now
            ),
            nullable=False
        ),
        sa.Column(
            'ai_feedback_level',
            sa.Enum(
                'basic',
                'concise',
                'full_in_depth',
                name='aifeedbacklevel'
            ),
            nullable=False
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('first_name', sa.String(), nullable=False),
        sa.Column('last_name', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=False),
        sa.Column('password_hash', sa.String(), nullable=False),
        sa.Column(
            'role',
            sa.Enum(
                'user',
                'admin',
                name='role'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column('plan_id', sa.UUID(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('is_email_verified', sa.Boolean(), nullable=True),
        sa.Column('email_verification_secret', sa.String(), nullable=True),
        sa.Column('password_reset_secret', sa.String(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.create_table(
        'study_materials',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('file_name', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('processed_content', sa.JSON(), nullable=True),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'processing',
                'completed',
                'failed',
                name='materialstatus'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'subscriptions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('plan_id', sa.UUID(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'active',
                'cancelled',
                'expired',
                name='subscriptionstatus'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'usage_tracking',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('uploads_count', sa.Integer(), nullable=False),
        sa.Column('assessments_count', sa.Integer(), nullable=False),
        sa.Column('asked_questions_count', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'assessment_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('material_id', sa.UUID(), nullable=False),
        sa.Column('topic', sa.String(), nullable=True),
        sa.Column(
            'difficulty',
            sa.Enum(
                'easy',
                'medium',
                'hard',
                name='difficulty'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column('question_types', sa.JSON(), nullable=False),
        sa.Column('questions_payload', sa.JSON(), nullable=False),
        sa.Column('current_index', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'in_progress',
                'completed',
                name='sessionstatus'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['material_id'], ['study_materials.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'transactions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('subscription_id', sa.UUID(), nullable=True),
        sa.Column('reference', sa.String(), nullable=False),
        sa.Column('authorization_url', sa.String(), nullable=True),
        sa.Column('amount_pence', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'pending',
                'success',
                'failed',
                name='transactionstatus'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('reference'),
    )

    op.create_table(
        'submissions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('question_index', sa.Integer(), nullable=False),
        sa.Column(
            'question_type',
            sa.Enum(
                'multiple_choice',
                'true_false',
                'short_answer',
                name='questiontype'  # auto‐CREATE
            ),
            nullable=False
        ),
        sa.Column('student_answer', sa.Text(), nullable=False),
        sa.Column('correct_answer', sa.Text(), nullable=True),
        sa.Column('feedback', sa.JSON(), nullable=True),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=True
        ),
        sa.ForeignKeyConstraint(['session_id'], ['assessment_sessions.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # The autogenerated drop order for tables is fine:
    op.drop_table('submissions')
    op.drop_table('transactions')
    op.drop_table('assessment_sessions')
    op.drop_table('usage_tracking')
    op.drop_table('subscriptions')
    op.drop_table('study_materials')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_table('plans')

    # (Optionally) DROP the ENUM types if you want a full rollback to no‐ENUMs:
    # Wrapping each in “IF EXISTS” ensures downgrade never errors if the type is already gone.
    op.execute("DROP TYPE IF EXISTS questiontype CASCADE;")
    op.execute("DROP TYPE IF EXISTS transactionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS sessionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS difficulty CASCADE;")
    op.execute("DROP TYPE IF EXISTS subscriptionstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS materialstatus CASCADE;")
    op.execute("DROP TYPE IF EXISTS role CASCADE;")
    op.execute("DROP TYPE IF EXISTS aifeedbacklevel CASCADE;")
    op.execute("DROP TYPE IF EXISTS summarydetail CASCADE;")
