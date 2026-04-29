"""Add ladder tables

Revision ID: b9d0a4f2c3e1
Revises: 3e91c894b0a8
Create Date: 2026-04-26 12:00:00.000000

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "b9d0a4f2c3e1"
down_revision = "3e91c894b0a8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("config", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("ladder_channel_id", sa.BigInteger(), nullable=True)
        )

    op.create_table(
        "ladder",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("rotation_id", sa.String(), nullable=False),
        sa.Column(
            "maps_per_match",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "max_team_size",
            sa.Integer(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "max_challenge_distance",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "max_in_flight_per_team",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("leaderboard_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("leaderboard_message_id", sa.BigInteger(), nullable=True),
        sa.Column("history_channel_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["rotation_id"],
            ["rotation.id"],
            name=op.f("fk_ladder_rotation_id_rotation"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder")),
        sa.UniqueConstraint("name", name=op.f("uq_ladder_name")),
    )
    with op.batch_alter_table("ladder", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_created_at"), ["created_at"], unique=False
        )

    op.create_table(
        "ladder_team",
        sa.Column("ladder_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("captain_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("wins", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("losses", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("draws", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["captain_id"],
            ["player.id"],
            name=op.f("fk_ladder_team_captain_id_player"),
        ),
        sa.ForeignKeyConstraint(
            ["ladder_id"],
            ["ladder.id"],
            name=op.f("fk_ladder_team_ladder_id_ladder"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_team")),
        sa.UniqueConstraint(
            "ladder_id", "name", name=op.f("uq_ladder_team_ladder_id_name")
        ),
        sa.UniqueConstraint(
            "ladder_id", "position", name=op.f("uq_ladder_team_ladder_id_position")
        ),
    )
    with op.batch_alter_table("ladder_team", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_team_ladder_id"), ["ladder_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_team_created_at"), ["created_at"], unique=False
        )

    op.create_table(
        "ladder_team_player",
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_ladder_team_player_player_id_player"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["ladder_team.id"],
            name=op.f("fk_ladder_team_player_team_id_ladder_team"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_team_player")),
        sa.UniqueConstraint(
            "team_id",
            "player_id",
            name=op.f("uq_ladder_team_player_team_id_player_id"),
        ),
    )
    with op.batch_alter_table("ladder_team_player", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_team_player_team_id"), ["team_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_team_player_player_id"),
            ["player_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_team_player_joined_at"),
            ["joined_at"],
            unique=False,
        )

    op.create_table(
        "ladder_team_invite",
        sa.Column("team_id", sa.String(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("invited_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invited_by_id"],
            ["player.id"],
            name=op.f("fk_ladder_team_invite_invited_by_id_player"),
        ),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_ladder_team_invite_player_id_player"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["ladder_team.id"],
            name=op.f("fk_ladder_team_invite_team_id_ladder_team"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_team_invite")),
        sa.UniqueConstraint(
            "team_id",
            "player_id",
            name=op.f("uq_ladder_team_invite_team_id_player_id"),
        ),
    )
    with op.batch_alter_table("ladder_team_invite", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_team_invite_team_id"), ["team_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_team_invite_player_id"),
            ["player_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_team_invite_created_at"),
            ["created_at"],
            unique=False,
        )

    op.create_table(
        "ladder_match",
        sa.Column("ladder_id", sa.String(), nullable=False),
        sa.Column("challenger_team_id", sa.String(), nullable=False),
        sa.Column("defender_team_id", sa.String(), nullable=False),
        sa.Column("challenger_position_at_challenge", sa.Integer(), nullable=False),
        sa.Column("defender_position_at_challenge", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("winner_team_id", sa.String(), nullable=True),
        sa.Column(
            "challenger_map_wins",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "defender_map_wins",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("challenged_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["challenger_team_id"],
            ["ladder_team.id"],
            name=op.f("fk_ladder_match_challenger_team_id_ladder_team"),
        ),
        sa.ForeignKeyConstraint(
            ["defender_team_id"],
            ["ladder_team.id"],
            name=op.f("fk_ladder_match_defender_team_id_ladder_team"),
        ),
        sa.ForeignKeyConstraint(
            ["ladder_id"],
            ["ladder.id"],
            name=op.f("fk_ladder_match_ladder_id_ladder"),
        ),
        sa.ForeignKeyConstraint(
            ["winner_team_id"],
            ["ladder_team.id"],
            name=op.f("fk_ladder_match_winner_team_id_ladder_team"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_match")),
    )
    with op.batch_alter_table("ladder_match", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_match_ladder_id"), ["ladder_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_match_challenger_team_id"),
            ["challenger_team_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_match_defender_team_id"),
            ["defender_team_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_match_status"), ["status"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_ladder_match_challenged_at"),
            ["challenged_at"],
            unique=False,
        )

    op.create_table(
        "ladder_match_game",
        sa.Column("match_id", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("map_id", sa.String(), nullable=False),
        sa.Column("challenger_score", sa.Integer(), nullable=True),
        sa.Column("defender_score", sa.Integer(), nullable=True),
        sa.Column("winner_team", sa.Integer(), nullable=True),
        sa.Column("reported_at", sa.DateTime(), nullable=True),
        sa.Column("reported_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["map_id"],
            ["map.id"],
            name=op.f("fk_ladder_match_game_map_id_map"),
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["ladder_match.id"],
            name=op.f("fk_ladder_match_game_match_id_ladder_match"),
        ),
        sa.ForeignKeyConstraint(
            ["reported_by_id"],
            ["player.id"],
            name=op.f("fk_ladder_match_game_reported_by_id_player"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ladder_match_game")),
        sa.UniqueConstraint(
            "match_id",
            "ordinal",
            name=op.f("uq_ladder_match_game_match_id_ordinal"),
        ),
    )
    with op.batch_alter_table("ladder_match_game", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_ladder_match_game_match_id"),
            ["match_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("ladder_match_game", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_match_game_match_id"))
    op.drop_table("ladder_match_game")

    with op.batch_alter_table("ladder_match", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_match_challenged_at"))
        batch_op.drop_index(batch_op.f("ix_ladder_match_status"))
        batch_op.drop_index(batch_op.f("ix_ladder_match_defender_team_id"))
        batch_op.drop_index(batch_op.f("ix_ladder_match_challenger_team_id"))
        batch_op.drop_index(batch_op.f("ix_ladder_match_ladder_id"))
    op.drop_table("ladder_match")

    with op.batch_alter_table("ladder_team_invite", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_team_invite_created_at"))
        batch_op.drop_index(batch_op.f("ix_ladder_team_invite_player_id"))
        batch_op.drop_index(batch_op.f("ix_ladder_team_invite_team_id"))
    op.drop_table("ladder_team_invite")

    with op.batch_alter_table("ladder_team_player", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_team_player_joined_at"))
        batch_op.drop_index(batch_op.f("ix_ladder_team_player_player_id"))
        batch_op.drop_index(batch_op.f("ix_ladder_team_player_team_id"))
    op.drop_table("ladder_team_player")

    with op.batch_alter_table("ladder_team", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_team_created_at"))
        batch_op.drop_index(batch_op.f("ix_ladder_team_ladder_id"))
    op.drop_table("ladder_team")

    with op.batch_alter_table("ladder", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ladder_created_at"))
    op.drop_table("ladder")

    with op.batch_alter_table("config", schema=None) as batch_op:
        batch_op.drop_column("ladder_channel_id")
