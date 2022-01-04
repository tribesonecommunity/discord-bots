"""Add map voting

Revision ID: 6ff3945a5449
Revises: f16fb3ac6ddb
Create Date: 2022-01-03 16:58:35.650896

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "6ff3945a5449"
down_revision = "f16fb3ac6ddb"
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table(
        "current_map",
        sa.Column("map_rotation_index", sa.Integer(), nullable=True),
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_current_map")),
    )
    with op.batch_alter_table("current_map", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_current_map_created_at"),
            ["created_at"],
            unique=False,
        )

    op.create_table(
        "rotation_map",
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rotation_map")),
    )
    with op.batch_alter_table("rotation_map", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_rotation_map_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_rotation_map_full_name"), ["full_name"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_rotation_map_short_name"),
            ["short_name"],
            unique=True,
        )

    op.create_table(
        "voteable_map",
        sa.Column("full_name", sa.String(), nullable=True),
        sa.Column("short_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voteable_map")),
    )
    with op.batch_alter_table("voteable_map", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_voteable_map_created_at"),
            ["created_at"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_voteable_map_full_name"), ["full_name"], unique=True
        )
        batch_op.create_index(
            batch_op.f("ix_voteable_map_short_name"),
            ["short_name"],
            unique=True,
        )

    op.create_table(
        "map_vote",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("voteable_map_id", sa.String(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_map_vote_player_id_player"),
        ),
        sa.ForeignKeyConstraint(
            ["voteable_map_id"],
            ["voteable_map.id"],
            name=op.f("fk_map_vote_voteable_map_id_voteable_map"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_map_vote")),
        sa.UniqueConstraint(
            "player_id", "voteable_map_id", name=op.f("uq_map_vote_player_id")
        ),
    )
    with op.batch_alter_table("map_vote", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_map_vote_player_id"), ["player_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_map_vote_voteable_map_id"),
            ["voteable_map_id"],
            unique=False,
        )

    op.create_table(
        "skip_map_vote",
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["player_id"],
            ["player.id"],
            name=op.f("fk_skip_map_vote_player_id_player"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skip_map_vote")),
    )
    with op.batch_alter_table("skip_map_vote", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_skip_map_vote_player_id"),
            ["player_id"],
            unique=True,
        )

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("skip_map_vote", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_skip_map_vote_player_id"))

    op.drop_table("skip_map_vote")
    with op.batch_alter_table("map_vote", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_map_vote_voteable_map_id"))
        batch_op.drop_index(batch_op.f("ix_map_vote_player_id"))

    op.drop_table("map_vote")
    with op.batch_alter_table("voteable_map", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_voteable_map_short_name"))
        batch_op.drop_index(batch_op.f("ix_voteable_map_full_name"))
        batch_op.drop_index(batch_op.f("ix_voteable_map_created_at"))

    op.drop_table("voteable_map")
    with op.batch_alter_table("rotation_map", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rotation_map_short_name"))
        batch_op.drop_index(batch_op.f("ix_rotation_map_full_name"))
        batch_op.drop_index(batch_op.f("ix_rotation_map_created_at"))

    op.drop_table("rotation_map")
    with op.batch_alter_table("current_map", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_current_map_created_at"))

    op.drop_table("current_map")
    # ### end Alembic commands ###
