"""make last game finished at non-nullable

Revision ID: eac69ad70805
Revises: ef83c87be4de
Create Date: 2024-05-04 09:21:41.394787

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql.expression import text
from sqlalchemy.sql.functions import now

# revision identifiers, used by Alembic.
revision = "eac69ad70805"
down_revision = "ef83c87be4de"
branch_labels = None
depends_on = None


def upgrade():
    # Note, this is non-reversible  
    # If you downgrade, the rows that were previously null will not lose their value
    op.execute("UPDATE player_category_trueskill SET last_game_finished_at = NOW() WHERE last_game_finished_at IS NULL")
    with op.batch_alter_table("player_category_trueskill", schema=None) as batch_op:
        batch_op.alter_column("last_game_finished_at", 
                              existing_type=sa.DateTime(), 
                              nullable=False, 
                              server_default=now())


def downgrade():
    with op.batch_alter_table("player_category_trueskill", schema=None) as batch_op:
        batch_op.alter_column("last_game_finished_at", 
                              existing_type=sa.DateTime(), 
                              nullable=True)
