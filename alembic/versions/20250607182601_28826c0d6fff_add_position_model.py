"""Add position model

Revision ID: 28826c0d6fff
Revises: 54080d3503e2
Create Date: 2025-06-07 18:26:01.735967

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "28826c0d6fff"
down_revision = "54080d3503e2"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "position",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_position")),
    )
    with op.batch_alter_table("position", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_position_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_position_name"), ["name"], unique=True)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table("queue_position")
    with op.batch_alter_table("position", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_position_name"))
        batch_op.drop_index(batch_op.f("ix_position_created_at"))

    op.drop_table("position")
    # ### end Alembic commands ###
