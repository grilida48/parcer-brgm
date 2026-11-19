"""Initial CRM schema, frozen at revision 0001."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('settings',
        sa.Column('key', sa.String(length=100), nullable=False, primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
    )
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
    )
    op.create_table('activities',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(length=200), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table('searches',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('criteria', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('progress', sa.Integer(), nullable=False),
        sa.Column('found', sa.Integer(), nullable=False),
        sa.Column('errors', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
    )
    op.create_table('sessions',
        sa.Column('token_hash', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table('leads',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('category', sa.String(length=300), nullable=True),
        sa.Column('city', sa.String(length=200), nullable=True),
        sa.Column('address', sa.Text(), nullable=True),
        sa.Column('phone', sa.String(length=100), nullable=True),
        sa.Column('website', sa.Text(), nullable=True),
        sa.Column('rating', sa.Float(), nullable=True),
        sa.Column('review_count', sa.Integer(), nullable=True),
        sa.Column('socials', sa.JSON(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('card_url', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('source_id', sa.String(length=200), nullable=True),
        sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('website_status', sa.String(length=30), nullable=False),
        sa.Column('audit', sa.JSON(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=False),
        sa.Column('score_reasons', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=100), nullable=False),
        sa.Column('owner_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('order_value', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('next_contact', sa.String(length=40), nullable=True),
        sa.Column('consent', sa.String(length=20), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('export_allowed', sa.Boolean(), nullable=False),
        sa.Column('is_chain', sa.Boolean(), nullable=True),
        sa.Column('search_id', sa.Integer(), sa.ForeignKey('searches.id'), nullable=True),
        sa.Column('fingerprint', sa.String(length=64), nullable=False, unique=True),
    )
    op.create_index('ix_leads_score', 'leads', ['score'], unique=False)
    op.create_index('ix_leads_city', 'leads', ['city'], unique=False)
    op.create_index('ix_leads_phone', 'leads', ['phone'], unique=False)
    op.create_index('ix_leads_status', 'leads', ['status'], unique=False)
    op.create_index('ix_leads_owner_id', 'leads', ['owner_id'], unique=False)
    op.create_table('interactions',
        sa.Column('id', sa.Integer(), nullable=False, primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('kind', sa.String(length=30), nullable=False),
        sa.Column('comment', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_interactions_lead_id', 'interactions', ['lead_id'], unique=False)
    op.create_table('search_leads',
        sa.Column('search_id', sa.Integer(), sa.ForeignKey('searches.id'), nullable=False, primary_key=True),
        sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False, primary_key=True),
    )


def downgrade():
    op.drop_table('search_leads')
    op.drop_table('interactions')
    op.drop_table('leads')
    op.drop_table('sessions')
    op.drop_table('searches')
    op.drop_table('activities')
    op.drop_table('users')
    op.drop_table('settings')
