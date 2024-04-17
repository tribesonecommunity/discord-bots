"""add last finished game timestamp to player_category_trueskill

Revision ID: 6ec201ec28d3
Revises: 677c768bdcb5
Create Date: 2024-04-17 21:10:57.679064

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6ec201ec28d3"
down_revision = "677c768bdcb5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("player_category_trueskill", schema=None) as batch_op:
        batch_op.add_column(sa.Column("last_game_finished_at", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("player_category_trueskill", schema=None) as batch_op:
        batch_op.drop_column("last_game_finished_at")
