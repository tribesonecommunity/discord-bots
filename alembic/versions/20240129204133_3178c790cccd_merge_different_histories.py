"""merge different histories

Revision ID: 3178c790cccd
Revises: 59668fb6ffca, 406862e3e08a
Create Date: 2024-01-29 20:41:33.449834

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "3178c790cccd"
down_revision = ("59668fb6ffca", "406862e3e08a")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
