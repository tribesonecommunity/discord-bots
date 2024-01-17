"""Add raffle table

Revision ID: e0ca611b0ef1
Revises: 3fe20acb942a
Create Date: 2023-08-26 11:22:32.731412

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e0ca611b0ef1"
down_revision = "3fe20acb942a"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "raffle",
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("winning_player_id", sa.Integer(), nullable=True),
        sa.Column("total_tickets", sa.Integer(), nullable=False),
        sa.Column(
            "winning_player_total_tickets", sa.Integer(), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["winning_player_id"],
            ["player.id"],
            name=op.f("fk_raffle_winning_player_id_player"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_raffle")),
    )
    with op.batch_alter_table("raffle", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_raffle_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_raffle_total_tickets"),
            ["total_tickets"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_raffle_winning_player_id"),
            ["winning_player_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_raffle_winning_player_total_tickets"),
            ["winning_player_total_tickets"],
            unique=False,
        )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("raffle", schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f("ix_raffle_winning_player_total_tickets")
        )
        batch_op.drop_index(batch_op.f("ix_raffle_winning_player_id"))
        batch_op.drop_index(batch_op.f("ix_raffle_total_tickets"))
        batch_op.drop_index(batch_op.f("ix_raffle_created_at"))

    op.drop_table("raffle")
    # ### end Alembic commands ###
