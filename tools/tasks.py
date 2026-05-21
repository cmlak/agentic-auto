
# tools/tasks.py
import os
import subprocess
from django.conf import settings
from celery import shared_task
# Import your Tenant routing model here, e.g.:
# from portal.models import Tenant 

@shared_task
def backup_all_tenant_schemas():
    db_config = settings.DATABASES['default']
    db_host = db_config.get('HOST', '')
    db_port = db_config.get('PORT', '')
    db_user = db_config.get('USER', 'postgres')
    db_name = db_config.get('NAME', 'postgres')

    if not db_host or '/' in db_host or 'cloudsql' in db_host:
        db_host = "/cloudsql/document-project-464509:asia-southeast1:agentic-platform-2"
        db_port = "5432"

    # 💡 DYNAMIC FIX: Query the actual schema strings directly from your DB
    # or use .lower() if you want to fall back to a hardcoded list safely:
    # schemas_to_backup = ['public', 'cckt', 'abc']
    
    try:
        from django_tenants.utils import get_tenant_model
        schemas_to_backup = [t.schema_name for t in get_tenant_model().objects.all()]
        if 'public' not in schemas_to_backup:
            schemas_to_backup.append('public')
    except ImportError:
        # Fallback to lowercase string list if django-tenants utilities aren't used
        schemas_to_backup = ['public', 'cckt', 'abc']

    for schema_name in schemas_to_backup:
        backup_file_path = f"/tmp/{schema_name}_backup.dump"
        
        cmd = [
            'pg_dump',
            '-h', db_host,
            '-p', str(db_port),
            '-U', db_user,
            '-d', db_name,
            '-n', schema_name,  # Passed with verified correct casing
            '-F', 'c',
            '-f', backup_file_path
        ]

        env = os.environ.copy()
        env['PGPASSWORD'] = db_config.get('PASSWORD', '')

        try:
            print(f"Starting dump for schema: {schema_name}...")
            subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)
            print(f"✅ Schema {schema_name} successfully dumped to {backup_file_path}")
            
            # Your upload code block here...
            
        except subprocess.CalledProcessError as e:
            print(f"❌ pg_dump failed for schema {schema_name}: {e.stderr}")