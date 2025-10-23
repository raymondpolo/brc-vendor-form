"""
migrate_uploads_to_s3.py

Small script to upload local uploads/ directory contents to S3 bucket.
Usage:
    python scripts/migrate_uploads_to_s3.py --bucket my-bucket --prefix uploads/ --dry-run

It will iterate files under the project's uploads/ directory and upload them to S3 using the same filename as the DB 'filename' field.
Ensure AWS credentials are available via environment variables or AWS CLI config.
"""
import os
import argparse
import logging
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except Exception:
    boto3 = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[1]
UPLOADS_DIR = BASE_DIR / 'uploads'


def main():
    parser = argparse.ArgumentParser(description='Upload local uploads/ files to S3')
    parser.add_argument('--bucket', '-b', required=True, help='S3 bucket name')
    parser.add_argument('--prefix', '-p', default='', help='Optional S3 prefix/key prefix (e.g. "uploads/")')
    parser.add_argument('--dry-run', action='store_true', help='Do not perform uploads, only show what would be done')
    parser.add_argument('--delete-local', action='store_true', help='After successful upload, delete local files (use with caution)')
    args = parser.parse_args()

    if boto3 is None:
        logger.error('boto3 is not installed. Install requirements.txt before running this script.')
        return

    s3 = boto3.client('s3')
    bucket = args.bucket
    prefix = args.prefix or ''

    if not UPLOADS_DIR.exists():
        logger.error(f'Uploads directory not found at {UPLOADS_DIR}')
        return

    files = list(UPLOADS_DIR.rglob('*'))
    files = [f for f in files if f.is_file()]
    if not files:
        logger.info('No files found in uploads/ to migrate.')
        return

    logger.info(f'Found {len(files)} files to consider for upload to s3://{bucket}/{prefix}')

    for f in files:
        rel = f.relative_to(UPLOADS_DIR)
        s3_key = prefix + str(rel).replace('\\', '/')
        logger.info(f'Uploading {f} -> s3://{bucket}/{s3_key}')
        if args.dry_run:
            continue
        try:
            with open(f, 'rb') as fh:
                s3.upload_fileobj(fh, bucket, s3_key)
            logger.info(f'Uploaded {f} to s3://{bucket}/{s3_key}')
            if args.delete_local:
                try:
                    os.remove(f)
                    logger.info(f'Deleted local file {f}')
                except Exception as de:
                    logger.warning(f'Failed deleting local file {f}: {de}')
        except ClientError as e:
            logger.error(f'Failed to upload {f}: {e}')

    logger.info('Migration complete.')


if __name__ == '__main__':
    main()
