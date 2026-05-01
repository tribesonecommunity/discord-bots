"""Add ladder challenge cooldown

Revision ID: a4f8c1d92b75
Revises: b9d0a4f2c3e1
Create Date: 2026-05-01 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a4f8c1d92b75"
down_revision = "b9d0a4f2c3e1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("ladder", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "challenge_cooldown_hours",
                sa.Integer(),
                server_default=sa.text("168"),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("ladder", schema=None) as batch_op:
        batch_op.drop_column("challenge_cooldown_hours")
