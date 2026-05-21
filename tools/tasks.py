import os
import subprocess
from django.conf import settings
from celery import shared_task

@shared_task
def backup_all_tenant_schemas():
    # 1. Pull down your default connection settings group
    db_config = settings.DATABASES['default']
    
    db_host = db_config.get('HOST', '')
    db_port = db_config.get('PORT', '')
    db_user = db_config.get('USER', 'postgres')
    db_name = db_config.get('NAME', 'postgres')

    # Fallback to local Cloud Run socket proxy path if credentials are blank or path-based
    if not db_host or '/' in db_host or 'cloudsql' in db_host:
        db_host = "/cloudsql/document-project-464509:asia-southeast1:agentic-platform-2"
        db_port = "5432"

    # 2. Your loop through your tenant target blocks
    schemas_to_backup = ['CCKT', 'public', 'ABC']
    
    for schema_name in schemas_to_backup:
        backup_file_path = f"/tmp/{schema_name}_backup.dump"
        
        # 💡 The cmd block MUST live inside this loop so 'schema_name' is defined!
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-n', schema_name,  # Handled safely on each iteration now
            '-F', 'c',
            '-f', backup_file_path
        ]

        # Inject matching system flags safely
        env = os.environ.copy()
        env['PGPASSWORD'] = db_config.get('PASSWORD', '')

        try:
            print(f"Starting dump for schema: {schema_name}...")
            result = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
            # Add your Cloud Storage upload code right here...
            
        except subprocess.CalledProcessError as e:
            print(f"❌ pg_dump failed for schema {schema_name}: {e.stderr}")