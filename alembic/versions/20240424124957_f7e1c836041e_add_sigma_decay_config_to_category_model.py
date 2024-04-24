"""add sigma decay config to Category model

Revision ID: f7e1c836041e
Revises: 950bfa890620
Create Date: 2024-04-24 12:49:57.504768

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f7e1c836041e"
down_revision = "950bfa890620"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("category", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "sigma_decay_amount",
                sa.Float(),
                server_default=sa.text("0.0"),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "sigma_decay_grace_days",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            )
        )


def downgrade():
    with op.batch_alter_table("category", schema=None) as batch_op:
        batch_op.drop_column("sigma_decay_grace_days")
        batch_op.drop_column("sigma_decay_amount")
