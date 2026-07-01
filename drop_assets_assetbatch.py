import os
import sys

# Setup django environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'agentic_platform.settings')

import django
django.setup()

from django.db import connection

def drop_tables():
    with connection.cursor() as cursor:
        cursor.execute("SELECT schema_name FROM information_schema.schemata;")
        schemas = cursor.fetchall()
        for (schema,) in schemas:
            if schema.startswith('pg_') or schema == 'information_schema':
                continue
            
            print(f"Processing schema {schema}...")
            
            # Drop table
            try:
                cursor.execute(f"DROP TABLE IF EXISTS {schema}.assets_assetbatch CASCADE;")
            except Exception as e:
                print(f"  Error dropping table in {schema}: {e}")
                
            # Clean migration history
            try:
                cursor.execute(f"SET search_path TO {schema}")
                cursor.execute("DELETE FROM django_migrations WHERE app='assets' AND name='0006_assetbatch';")
            except Exception as e:
                print(f"  No django_migrations in {schema}")

if __name__ == "__main__":
    drop_tables()
