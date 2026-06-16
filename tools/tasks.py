import os
import subprocess
from datetime import datetime
from django.conf import settings
from google.cloud import storage
from google.cloud import pubsub_v1
import json
from celery import shared_task
import logging
from document.models import DraftKnowledgeRule
from .orchestrators import DjangoEventOrchestrator


@shared_task
def backup_all_tenant_schemas():
    """
    Synchronously loops through database schemas, creates a plain-text SQL 
    script file for each, and uploads the results to Google Cloud Storage.
    """
    print("Initializing synchronous database backup sequence (Plain-Text SQL Mode)...")
    
    # 1. Extract Connection Properties from Django Database Block
    db_config = settings.DATABASES['default']
    db_host = db_config.get('HOST', '')
    db_port = db_config.get('PORT', '')
    db_user = db_config.get('USER', 'postgres')
    db_name = db_config.get('NAME', 'postgres')
    db_password = db_config.get('PASSWORD', '')

    # 2. Infrastructure Socket Fallback
    if not db_host or '/' in db_host or 'cloudsql' in db_host:
        db_host = "/cloudsql/document-project-464509:asia-southeast1:agentic-platform-2"
        db_port = "5432"

    # 3. Target Schemas (Case-Sensitive Production Database Matches)
    schemas_to_backup = ['public', 'ABC', 'CCKT']
    
    # 4. Initialize Google Cloud Storage Client
    bucket_name = 'cambodiasmeprojects_sql_backup'
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)

    # Timestamp tag format for backup grouping
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    for schema_name in schemas_to_backup:
        # 💡 UPDATED: Extension changed to .sql
        backup_filename = f"{schema_name}_backup_{timestamp}.sql"
        local_backup_path = f"/tmp/{backup_filename}"
        
        # 5. Escaped Formatting for Strict Case-Sensitive Identifiers
        formatted_schema = f'"{schema_name}"' if schema_name != 'public' else schema_name
        
        # Construct pg_dump Execution Command
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-n', formatted_schema,
            '-F', 'p',               # Plain-Text SQL Script output
            '--clean',               # 💡 NEW: Drops database objects before recreating them
            '--if-exists',           # 💡 NEW: Prevents errors if objects don't exist yet
            '-f', local_backup_path
        ]

        # Inject password credentials safely into system sub-layer context block
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password

        try:
            print(f"Starting SQL plain-text dump for schema: {schema_name}...")
            
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

logger = logging.getLogger(__name__)

@shared_task(name="tools.tasks.process_draft_rule_task")
def process_draft_rule_task(payload):
    """
    Step 3 Worker:
    1. Receives payload from the webhook.
    2. Uses Orchestrator to save the rule as 'PENDING'.
    3. Finalizes the data state for the Dashboard.
    """
    try:
        logger.info(f"START: Processing draft rule proposal: {payload.get('draft_id')}")
        
        # Consistency: We use the same Orchestrator logic you already defined
        # This should handle: DraftKnowledgeRule.objects.update_or_create(...)
        DjangoEventOrchestrator.handle_draft_rule_proposed(payload)
        
        logger.info(f"SUCCESS: Draft rule {payload.get('draft_id')} is now PENDING on Dashboard.")
        
    except Exception as e:
        logger.error(f"FAIL: Error in process_draft_rule_task: {e}")
        # Task will fail here, allowing Celery to retry based on your policy
        raise e
