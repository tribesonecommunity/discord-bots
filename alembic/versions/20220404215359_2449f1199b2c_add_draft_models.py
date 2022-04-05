"""Add draft models

Revision ID: 2449f1199b2c
Revises: e14c46b66983
Create Date: 2022-04-04 21:53:59.872531

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2449f1199b2c"
down_revision = "e14c46b66983"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "draft",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("signups_open", sa.Boolean(), nullable=False),
        sa.Column("checkins_open", sa.Boolean(), nullable=False),
        sa.Column("draft_open", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft")),
    )
    with op.batch_alter_table("draft", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_draft_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_draft_is_active"), ["is_active"], unique=False
        )

    op.create_table(
        "draft_captain",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("team_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_draft_captain_player_id_player"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft_captain")),
        sa.UniqueConstraint(
            "team_name", name=op.f("uq_draft_captain_team_name")
        ),
    )
    with op.batch_alter_table("draft_captain", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_draft_captain_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_draft_captain_player_id"),
            ["player_id"],
            unique=False,
        )

    op.create_table(
        "draft_player",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("draft_captain_id", sa.Integer(), nullable=True),
        sa.Column("is_checked_in", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_captain_id"],
            ["draft_captain.id"],
            name=op.f("fk_draft_player_draft_captain_id_draft_captain"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_draft_player_player_id_player"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft_player")),
    )
    with op.batch_alter_table("draft_player", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_draft_player_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_draft_player_draft_captain_id"),
            ["draft_captain_id"],
            unique=True,
        )
        batch_op.create_index(
            batch_op.f("ix_draft_player_is_checked_in"),
            ["is_checked_in"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_draft_player_player_id"), ["player_id"], unique=True
        )

    with op.batch_alter_table("finished_game_player", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_finished_game_player_player_name_player", type_="foreignkey"
        )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("finished_game_player", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_finished_game_player_player_name_player",
            "player",
            ["player_name"],
            ["id"],
        )

    with op.batch_alter_table("draft_player", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_draft_player_player_id"))
        batch_op.drop_index(batch_op.f("ix_draft_player_is_checked_in"))
        batch_op.drop_index(batch_op.f("ix_draft_player_draft_captain_id"))
        batch_op.drop_index(batch_op.f("ix_draft_player_created_at"))

    op.drop_table("draft_player")
    with op.batch_alter_table("draft_captain", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_draft_captain_player_id"))
        batch_op.drop_index(batch_op.f("ix_draft_captain_created_at"))

    op.drop_table("draft_captain")
    with op.batch_alter_table("draft", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_draft_is_active"))
        batch_op.drop_index(batch_op.f("ix_draft_created_at"))

    op.drop_table("draft")
    # ### end Alembic commands ###
