"""
check_attachments.py

Run this from the project root. It will import the Flask app, query Attachment records,
and test whether the stored filename exists locally under UPLOAD_FOLDER or in S3 (if configured).

Usage:
    python .\scripts\check_attachments.py

Set AWS env vars if you want S3 checks to run (AWS_S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY).
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app
from app.extensions import db
from app.models import Attachment

def check_s3_object(bucket, key):
    try:
        import boto3
        s3 = boto3.client('s3')
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False

def main():
    app = create_app()
    with app.app_context():
        upload_folder = app.config.get('UPLOAD_FOLDER') or os.path.join(str(PROJECT_ROOT), 'uploads')
        s3_bucket = os.environ.get('AWS_S3_BUCKET') or app.config.get('AWS_S3_BUCKET')

        attachments = Attachment.query.all()
        total = len(attachments)
        missing_local = 0
        found_s3_only = 0
        missing_both = 0

        print(f"Checking {total} attachments. Local upload folder: {upload_folder}")
        if s3_bucket:
            print(f"S3 bucket configured: {s3_bucket}")
        else:
            print("No S3 bucket configured. Only checking local filesystem.")

        print("\nid | filename | local_exists | s3_exists | work_order_id | file_type")
        print('-'*80)
        for a in attachments:
            fn = a.filename
            local_path = os.path.join(upload_folder, fn) if fn else None
            local_exists = bool(fn and os.path.exists(local_path))
            s3_exists = False
            if not local_exists and s3_bucket and fn:
                s3_exists = check_s3_object(s3_bucket, fn)

            if not local_exists:
                missing_local += 1
            if not local_exists and s3_exists:
                found_s3_only += 1
            if not local_exists and not s3_exists:
                missing_both += 1

            print(f"{a.id} | {fn} | {local_exists} | {s3_exists} | {a.work_order_id} | {a.file_type}")

        print('\nSummary:')
        print(f"Total attachments: {total}")
        print(f"Missing locally: {missing_local}")
        if s3_bucket:
            print(f"Found in S3 but not locally: {found_s3_only}")
            print(f"Missing both locally and on S3: {missing_both}")

if __name__ == '__main__':
    main()
