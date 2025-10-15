"""Add one-to-one relationship between quotes and attachments

Revision ID: d781d8aa2370
Revises: 799954dffc34
Create Date: 2025-09-09 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd781d8aa2370'
down_revision = '799954dffc34'
branch_labels = None
depends_on = None


def upgrade():
    # This block will handle the alteration of the 'quote' table.
    with op.batch_alter_table('quote', schema=None) as batch_op:
        # Add the new attachment_id column. It must be nullable initially for the data migration.
        batch_op.add_column(sa.Column('attachment_id', sa.Integer(), nullable=True))
        # Create the foreign key relationship to the 'attachment' table.
        batch_op.create_foreign_key('fk_quote_attachment_id_attachment', 'attachment', ['attachment_id'], ['id'])
        # Temporarily make the old 'filename' column nullable to avoid conflicts.
        batch_op.alter_column('filename', existing_type=sa.VARCHAR(length=255), nullable=True)

    # Execute a raw SQL statement to populate the new 'attachment_id' column.
    op.execute("""
        UPDATE quote
        SET attachment_id = (
            SELECT id FROM attachment
            WHERE attachment.filename = quote.filename AND attachment.file_type = 'Quote'
        )
    """)

    # Now that the data is migrated, modify the 'quote' table again.
    with op.batch_alter_table('quote', schema=None) as batch_op:
        # Make the 'attachment_id' column non-nullable as it's a required link.
        batch_op.alter_column('attachment_id', existing_type=sa.INTEGER(), nullable=False)
        # Drop the now-redundant 'filename' column from the 'quote' table.
        batch_op.drop_column('filename')

    # This block will handle the alteration of the 'vendor' table.
    # We introspect the database to see if the constraint exists before trying to drop it.
    # This avoids errors that would abort the transaction on PostgreSQL.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    constraints = insp.get_unique_constraints('vendor')
    if any(c['name'] == 'uq_vendor_email' for c in constraints):
        with op.batch_alter_table('vendor', schema=None) as batch_op:
            batch_op.drop_constraint('uq_vendor_email', type_='unique')
    else:
        # This print statement is helpful for logging during deployment.
        print("Constraint 'uq_vendor_email' not found on table 'vendor', skipping drop.")


def downgrade():
    # Revert the changes in the reverse order of the upgrade.
    # Similar to the upgrade, we check if the constraint exists before creating it
    # to make the downgrade process more robust.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    constraints = insp.get_unique_constraints('vendor')
    if not any(c['name'] == 'uq_vendor_email' for c in constraints):
        with op.batch_alter_table('vendor', schema=None) as batch_op:
            batch_op.create_unique_constraint('uq_vendor_email', ['email'])
    else:
        print("Constraint 'uq_vendor_email' already exists, skipping creation.")


    with op.batch_alter_table('quote', schema=None) as batch_op:
        batch_op.add_column(sa.Column('filename', sa.VARCHAR(length=255), nullable=True))

    op.execute("""
        UPDATE quote
        SET filename = (
            SELECT filename FROM attachment
            WHERE attachment.id = quote.attachment_id
        )
    """)
    
    with op.batch_alter_table('quote', schema=None) as batch_op:
        batch_op.alter_column('filename', existing_type=sa.VARCHAR(length=255), nullable=False)
        batch_op.drop_constraint('fk_quote_attachment_id_attachment', type_='foreignkey')
        batch_op.drop_column('attachment_id')

