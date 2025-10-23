Migrate uploads/ to S3

This folder contains a small script to migrate existing local uploads to an S3 bucket.

Prerequisites
- Python environment with `boto3` installed. Run:

  python -m pip install -r ../requirements.txt

- AWS credentials available in the environment or via AWS CLI config (~/.aws/credentials).

Usage (PowerShell)

Set environment variables or pass the bucket explicitly:

```powershell
# Example: dry-run (shows what would be uploaded)
python .\migrate_uploads_to_s3.py --bucket my-bucket --prefix uploads/ --dry-run

# Example: actually upload
python .\migrate_uploads_to_s3.py --bucket my-bucket --prefix uploads/

# Example: upload and delete local files after successful upload
python .\migrate_uploads_to_s3.py --bucket my-bucket --prefix uploads/ --delete-local
```

Notes
- The script uses the same filename/key structure relative to `uploads/` directory.
- Ensure `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` are set in your environment or configure the AWS CLI.
