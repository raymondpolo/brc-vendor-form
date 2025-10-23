"""Add original_filename and data to Attachment

Revision ID: 2f3c4d5e6b7a
Revises: 22c608b1a2a9
Create Date: 2025-10-23 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f3c4d5e6b7a'
down_revision = '22c608b1a2a9'
branch_labels = None
depends_on = None


def upgrade():
    # Add original_filename and data columns to attachment
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.add_column(sa.Column('original_filename', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('data', sa.LargeBinary(), nullable=True))


def downgrade():
    # Remove the columns on downgrade
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.drop_column('data')
        batch_op.drop_column('original_filename')
