"""Add guild model

Revision ID: 194f35739989
Revises: 0e0d14ee5e86
Create Date: 2024-02-10 18:32:13.552321

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "194f35739989"
down_revision = "0e0d14ee5e86"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "guild",
        sa.Column("discord_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guild")),
        sa.UniqueConstraint("name", name=op.f("uq_guild_name")),
    )
    with op.batch_alter_table("guild", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_guild_created_at"), ["created_at"], unique=False
        )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("guild", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_guild_created_at"))

    op.drop_table("guild")
    # ### end Alembic commands ###
