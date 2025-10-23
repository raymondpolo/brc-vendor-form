"""Merge heads: 9283892d6db2, 2f3c4d5e6b7a

Revision ID: 3a7b8c9d0e1f
Revises: 9283892d6db2, 2f3c4d5e6b7a
Create Date: 2025-10-23 16:20:00.000000

This is a merge revision created to resolve multiple head revisions present in the
repository. The migration does not change the database schema; it simply tells Alembic
that the branches have been merged.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3a7b8c9d0e1f'
down_revision = ('9283892d6db2', '2f3c4d5e6b7a')
branch_labels = None
depends_on = None


def upgrade():
    # Merge-only revision: no schema changes
    pass


def downgrade():
    # Downgrade would re-introduce multiple heads; not supported automatically
    pass
