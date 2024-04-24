"""add sigma decay config to Category model

Revision ID: ef83c87be4de
Revises: 8942dc29fa48
Create Date: 2024-04-24 16:48:55.275178

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ef83c87be4de"
down_revision = "8942dc29fa48"
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
