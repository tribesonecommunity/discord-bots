"""add move option to player and queue

Revision ID: b29ddf680a84
Revises: 537a7815efa5
Create Date: 2024-02-07 21:08:53.662450

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b29ddf680a84"
down_revision = "537a7815efa5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("player", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "move_enabled",
                sa.Boolean(),
                nullable=False,
            )
        )
    
    with op.batch_alter_table("queue", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "move_enabled",
                sa.Boolean(),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("player", schema=None) as batch_op:
        batch_op.drop_column("move_enabled")

    with op.batch_alter_table("queue", schema=None) as batch_op:
        batch_op.drop_column("move_enabled")
