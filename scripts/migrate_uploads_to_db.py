"""
migrate_uploads_to_db.py

Script to import files from the local uploads/ directory into the database Attachment.data column.
Usage:
    python scripts/migrate_uploads_to_db.py [--dry-run] [--delete-local]

This will iterate Attachment rows and for each where data is NULL, it will attempt to find the
corresponding file under the project's uploads/ directory using the Attachment.filename value.
If found, the file bytes are loaded into Attachment.data and the original filename is set if missing.
"""
import os
import argparse
import logging
from pathlib import Path

from app.extensions import db
from app.models import Attachment

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BASE_DIR / 'uploads'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description='Migrate local uploads into DB Attachment.data')
    parser.add_argument('--dry-run', action='store_true', help='Do not write changes to DB, only report')
    parser.add_argument('--delete-local', action='store_true', help='Delete local file after successful import')
    args = parser.parse_args()

    if not UPLOADS_DIR.exists():
        logger.error(f'Uploads directory not found at {UPLOADS_DIR}')
        return

    # Use Flask app context to access DB
    from app import create_app
    app = create_app()
    with app.app_context():
        attachments = Attachment.query.all()
        logger.info(f'Found {len(attachments)} attachment rows in DB')

        count = 0
        for att in attachments:
            if att.data:
                continue
            filename = att.filename
            file_path = UPLOADS_DIR / filename
            if not file_path.exists():
                # try with subpaths (in case filename contains directories)
                candidate = UPLOADS_DIR / Path(filename).name
                if candidate.exists():
                    file_path = candidate
                else:
                    logger.debug(f'Local file for attachment id={att.id} not found: {filename}')
                    continue

            logger.info(f'Importing {file_path} into attachment id={att.id}')
            if args.dry_run:
                count += 1
                continue

            try:
                with open(file_path, 'rb') as fh:
                    data = fh.read()
                att.data = data
                if not att.original_filename:
                    att.original_filename = Path(filename).name
                db.session.add(att)
                db.session.commit()
                count += 1
                logger.info(f'Imported attachment id={att.id} ({file_path})')
                if args.delete_local:
                    try:
                        os.remove(file_path)
                        logger.info(f'Deleted local file {file_path}')
                    except Exception as e:
                        logger.warning(f'Failed to delete local file {file_path}: {e}')
            except Exception as e:
                logger.exception(f'Failed to import {file_path} for attachment id={att.id}: {e}')

        logger.info(f'Import complete. {count} files processed.')


if __name__ == '__main__':
    main()
