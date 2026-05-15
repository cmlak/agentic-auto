import os
import subprocess
import logging
from datetime import datetime
from celery import shared_task
from django.conf import settings
from google.cloud import storage
from clients.models import Client 

logger = logging.getLogger(__name__)

@shared_task
def backup_all_tenant_schemas():
    """
    Iterates through all tenant schemas, runs pg_dump, 
    and uploads the backups to Google Cloud Storage.
    """
    # 1. Grab Database Credentials safely from Django's memory
    # Django has already parsed your DATABASE_URL in settings.py, so we just read the results!
    db_config = settings.DATABASES['default']
    db_name = db_config['NAME']
    db_user = db_config['USER']
    db_pass = db_config['PASSWORD']
    db_host = db_config['HOST']
    db_port = str(db_config.get('PORT', '5432'))

    # 2. Connect to Google Cloud Storage
    bucket_name = os.getenv('BACKUP_BUCKET_NAME', 'cambodiasmeprojects_sql_backup')
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    success_count = 0

    # 3. Iterate through every schema (including 'public')
    for tenant in Client.objects.all():
        schema = tenant.schema_name
        
        # We use .dump (custom PostgreSQL format) as it is compressed and best for pg_restore
        filename = f"{schema}_backup_{timestamp}.dump"
        
        # /tmp/ is the only writable directory in Cloud Run
        local_file_path = f"/tmp/{filename}" 

        # Securely pass the DB password to the subprocess via environment variables
        # We use 'shell_env' so we don't accidentally overwrite python's built-in 'env' module
        shell_env = os.environ.copy()
        shell_env['PGPASSWORD'] = db_pass

        # Build the exact pg_dump command
        # The '-n' flag is the magic multi-tenant command: it isolates just one schema!
        dump_cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', db_port,
            '-U', db_user,
            '-d', db_name,
            '-n', schema,     
            '-F', 'c',        # Custom format (compressed)
            '-f', local_file_path
        ]

        try:
            logger.info(f"Starting backup for schema: {schema}")
            
            # Execute the pg_dump command in the Linux shell
            subprocess.run(dump_cmd, env=shell_env, check=True)

            # Upload the resulting file to Google Cloud Storage
            gcs_path = f"database_backups/{schema}/{filename}"
            blob = bucket.blob(gcs_path)
            blob.upload_from_filename(local_file_path)
            
            logger.info(f"✅ Successfully uploaded {schema} backup to GCS: {gcs_path}")
            success_count += 1

        except subprocess.CalledProcessError as e:
            logger.error(f"❌ pg_dump failed for schema {schema}: {e}")
        except Exception as e:
            logger.error(f"❌ Error uploading schema {schema} to GCS: {e}")
        finally:
            # CLEANUP: Crucial step for Cloud Run to prevent memory leaks
            if os.path.exists(local_file_path):
                os.remove(local_file_path)

    return f"Backup complete! Successfully processed {success_count} schemas."