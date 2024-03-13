"""make in_progress-game_id nullable for:
in_progress_game_channel,
ecnonomy_transaction
remove from queue_waitlist

Revision ID: 4476251e82c3
Revises: 57702a59e6e5
Create Date: 2024-03-06 16:35:57.240278

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "4476251e82c3"
down_revision = "57702a59e6e5"
branch_labels = None
depends_on = None


def upgrade():
    pass
    """
    ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("economy_transaction", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_economy_transaction_in_progress_game_id_in_progress_game",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            batch_op.f("fk_economy_transaction_in_progress_game_id_in_progress_game"),
            "in_progress_game",
            ["in_progress_game_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("in_progress_game_channel", schema=None) as batch_op:
        batch_op.alter_column(
            "in_progress_game_id", existing_type=sa.VARCHAR(), nullable=True
        )
        batch_op.drop_constraint(
            "fk_in_progress_game_channel_in_progress_game_id_in_progress_game",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            batch_op.f(
                "fk_in_progress_game_channel_in_progress_game_id_in_progress_game"
            ),
            "in_progress_game",
            ["in_progress_game_id"],
            ["id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("queue_waitlist", schema=None) as batch_op:
        batch_op.drop_constraint(
            "uq_queue_waitlist_in_progress_game_id", type_="unique"
        )
        batch_op.drop_constraint(
            "fk_queue_waitlist_in_progress_game_id_in_progress_game",
            type_="foreignkey",
        )
        batch_op.drop_column("in_progress_game_id")

    # ### end Alembic commands ###
    """


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("queue_waitlist", schema=None) as batch_op:
        batch_op.execute("PRAGMA foreign_keys=OFF")  # only works for sqlite
        batch_op.add_column(
            sa.Column(
                "in_progress_game_id",
                sa.VARCHAR(),
                autoincrement=False,
                nullable=False,
            )
        )
        batch_op.create_foreign_key(
            "fk_queue_waitlist_in_progress_game_id_in_progress_game",
            "in_progress_game",
            ["in_progress_game_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_queue_waitlist_in_progress_game_id", ["in_progress_game_id"]
        )

    with op.batch_alter_table("in_progress_game_channel", schema=None) as batch_op:
        batch_op.execute("PRAGMA foreign_keys=OFF")  # only works for sqlite
        batch_op.drop_constraint(
            batch_op.f(
                "fk_in_progress_game_channel_in_progress_game_id_in_progress_game"
            ),
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_in_progress_game_channel_in_progress_game_id_in_progress_game",
            "in_progress_game",
            ["in_progress_game_id"],
            ["id"],
        )
        batch_op.alter_column(
            "in_progress_game_id", existing_type=sa.VARCHAR(), nullable=False
        )

    with op.batch_alter_table("economy_transaction", schema=None) as batch_op:
        batch_op.execute("PRAGMA foreign_keys=OFF")  # only works for sqlite
        batch_op.drop_constraint(
            batch_op.f("fk_economy_transaction_in_progress_game_id_in_progress_game"),
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_economy_transaction_in_progress_game_id_in_progress_game",
            "in_progress_game",
            ["in_progress_game_id"],
            ["id"],
        )

    # ### end Alembic commands ###
