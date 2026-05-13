from django.core.management.base import BaseCommand
from django.db import connection, transaction

class Command(BaseCommand):
    help = 'Migrates data from public schema ghost tables to a specific tenant schema.'

    def handle(self, *args, **kwargs):
        # --- CONFIGURATION ---
        OLD_CLIENT_ID = 1          
        
        # 1. The exact name for the Information Schema (NO double quotes)
        TARGET_SCHEMA_NAME = 'CCKT' 
        
        # 2. The formatted name for SQL execution (WITH double quotes)
        SCHEMA_SQL = f'"{TARGET_SCHEMA_NAME}"'

        # ORDER MATTERS: Independent tables first, dependent tables last.
        tables = [
            'tools_client',               # Added to satisfy foreign key constraints
            'account_clientpromptmemo',   # Added: Independent config table
            'tools_vendor',
            'sale_customer',
            'account_account', # Added: Depends on account_account
            # 'account_accountmappingrule', # Added: Depends on account_account
            'tools_purchase',
            'tools_journalvoucher',
            'tools_old',
            'tools_adjustment',
            'sale_sale',
            'cash_bank',
            'cash_cash',
            'account_journalentry', 
            'account_journalline',        # Added: Depends on journalentry and account
        ]

        self.stdout.write(f"🚀 Starting migration for Client ID {OLD_CLIENT_ID} to schema {SCHEMA_SQL}...")

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    
                    for table in tables:
                        self.stdout.write(f"\nProcessing {table}...")

                        # 1. Check if table exists in target schema (Using double-quoted name)
                        cursor.execute(f"SELECT to_regclass('{SCHEMA_SQL}.{table}')")
                        if not cursor.fetchone()[0]:
                            self.stdout.write(self.style.WARNING(f"  ⚠️ Table {table} not found in {TARGET_SCHEMA_NAME}. Skipping."))
                            continue

                        # 2. Get columns of TARGET table (Using raw name WITHOUT double quotes!)
                        cursor.execute(f"""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = '{TARGET_SCHEMA_NAME}' AND table_name = '{table}'
                        """)
                        target_columns = [row[0] for row in cursor.fetchall()]
                        
                        if not target_columns:
                            self.stdout.write(self.style.ERROR(f"  ✖ FATAL: Could not find columns for {table}. Aborting to protect data."))
                            raise Exception("Column mapping failed.")
                            
                        col_string = ", ".join(target_columns)

                        # 3. Copy Data from Public to Target Schema (Using double-quoted name)
                        if table == 'account_journalline':
                            # account_journalline doesn't have client_id, so we join with account_journalentry
                            select_cols = ", ".join([f"public.{table}.{col}" for col in target_columns])
                            insert_sql = f"""
                                INSERT INTO {SCHEMA_SQL}.{table} ({col_string})
                                SELECT {select_cols}
                                FROM public.{table}
                                JOIN public.account_journalentry ON public.{table}.journal_entry_id = public.account_journalentry.id
                                WHERE public.account_journalentry.client_id = %s
                            """
                        elif table == 'tools_client':
                            insert_sql = f"""
                                INSERT INTO {SCHEMA_SQL}.{table} ({col_string})
                                SELECT {col_string}
                                FROM public.{table}
                                WHERE id = %s
                                ON CONFLICT (id) DO NOTHING
                            """
                        elif table == 'account_account':
                            insert_sql = f"""
                                INSERT INTO {SCHEMA_SQL}.{table} ({col_string})
                                SELECT {col_string}
                                FROM public.{table}
                                WHERE client_id = %s OR client_id IS NULL
                            """
                        else:
                            insert_sql = f"""
                                INSERT INTO {SCHEMA_SQL}.{table} ({col_string})
                                SELECT {col_string}
                                FROM public.{table}
                                WHERE client_id = %s
                            """
                        try:
                            cursor.execute(insert_sql, [OLD_CLIENT_ID])
                            row_count = cursor.rowcount
                            self.stdout.write(self.style.SUCCESS(f"  ✔ Copied {row_count} rows."))

                            # 4. Reset Primary Key Sequence
                            if row_count > 0:
                                cursor.execute(f"SELECT setval(pg_get_serial_sequence('{SCHEMA_SQL}.{table}', 'id'), coalesce(max(id), 1), max(id) IS NOT null) FROM {SCHEMA_SQL}.{table};")

                            # 5. Populate django-simple-history table
                            app_name, model_name = table.split('_', 1)
                            hist_table = f"{app_name}_historical{model_name}"
                            
                            cursor.execute(f"SELECT to_regclass('{SCHEMA_SQL}.{hist_table}')")
                            if cursor.fetchone()[0] and row_count > 0:
                                self.stdout.write(f"  -> Initializing audit trail in {hist_table}...")
                                hist_insert = f"""
                                    INSERT INTO {SCHEMA_SQL}.{hist_table} ({col_string}, history_date, history_type)
                                    SELECT {col_string}, NOW(), '+'
                                    FROM {SCHEMA_SQL}.{table}
                                """
                                cursor.execute(hist_insert)
                                self.stdout.write(self.style.SUCCESS(f"  ✔ Audit trail created."))

                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  ✖ SQL Error on {table}: {e}"))
                            raise e 

            self.stdout.write(self.style.SUCCESS(f"\n🎉 Migration complete for {TARGET_SCHEMA_NAME}! All data safely copied."))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n🛑 Migration aborted. All changes rolled back. Error: {e}"))