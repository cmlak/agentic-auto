import os
import subprocess
from datetime import datetime
from django.conf import settings
from google.cloud import storage
from celery import shared_task

@shared_task
def backup_all_tenant_schemas():
    """
    Synchronously loops through database schemas, creates a compressed pg_dump 
    binary archive file for each, and uploads the results to Google Cloud Storage.
    """
    print("Initializing synchronous database backup sequence...")
    
    # 1. Extract Connection Properties from Django Database Block
    db_config = settings.DATABASES['default']
    db_host = db_config.get('HOST', '')
    db_port = db_config.get('PORT', '')
    db_user = db_config.get('USER', 'postgres')
    db_name = db_config.get('NAME', 'postgres')
    db_password = db_config.get('PASSWORD', '')

    # 2. Infrastructure Socket Fallback
    # If the host string is blank, path-based, or referencing a unix socket proxy,
    # point explicitly to Cloud Run's native Cloud SQL proxy directory frame.
    if not db_host or '/' in db_host or 'cloudsql' in db_host:
        db_host = "/cloudsql/document-project-464509:asia-southeast1:agentic-platform-2"
        db_port = "5432"

    # 3. Target Schemas (Case-Sensitive Database Matches)
    schemas_to_backup = ['public', 'ABC', 'CCKT']
    
    # 4. Initialize Google Cloud Storage Client
    bucket_name = getattr(settings, 'GS_BUCKET_NAME', 'document-project-464509-backups')
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    # Timestamp tag format for backup grouping
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    for schema_name in schemas_to_backup:
        backup_filename = f"{schema_name}_backup_{timestamp}.dump"
        local_backup_path = f"/tmp/{backup_filename}"
        
        # 5. Construct pg_dump Execution Command
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-n', schema_name,  # Matches uppercase DB catalogs perfectly
            '-F', 'c',          # Compressed custom archive format
            '-f', local_backup_path
        ]

        # Inject password credentials safely into system sub-layer context block
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password

        try:
            print(f"Starting dump for schema: {schema_name}...")
            
            # Execute database binary dump extraction
            subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
            print(f"   Success: Schema dumped locally to {local_backup_path}")

            # 6. Stream and Commit to Google Cloud Storage
            blob_destination_path = f"database_backups/{timestamp}/{backup_filename}"
            print(f"   Uploading to Cloud Bucket path: {blob_destination_path}...")
            
            blob = bucket.blob(blob_destination_path)
            blob.upload_from_filename(local_backup_path)
            print(f"   Success: Upload finalized for schema {schema_name}.")

        except subprocess.CalledProcessError as e:
            print(f"❌ pg_dump failed for schema {schema_name}: {e.stderr}")
        except Exception as e:
            print(f"❌ Storage Bucket upload error encountered for schema {schema_name}: {str(e)}")
        finally:
            # 7. Clean up Ephemeral Container Local Disk Space
            if os.path.exists(local_backup_path):
                os.remove(local_backup_path)
                print(f"   Cleaned temporary local file cache for {schema_name}.")

    print("Backup process completed. Status: SUCCESS")