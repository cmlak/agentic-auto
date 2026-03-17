from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from cash.models import Bank, Cash

class Command(BaseCommand):
    help = 'Backfills debit_account_id and credit_account_id for existing Bank and Cash records based on their Journal Lines.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("--- Starting backfill for Bank records ---"))
        
        # Find Bank records missing either debit or credit account IDs
        banks = Bank.objects.filter(
            Q(debit_account_id__isnull=True) | Q(debit_account_id='') |
            Q(credit_account_id__isnull=True) | Q(credit_account_id='')
        )
        
        bank_count = 0
        with transaction.atomic():
            for bank in banks:
                je = bank.journal_entries.first()
                if je and je.lines.exists():
                    for line in je.lines.all():
                        if line.debit > 0:
                            bank.debit_account_id = line.account.account_id
                        if line.credit > 0:
                            bank.credit_account_id = line.account.account_id
                    
                    if bank.debit_account_id or bank.credit_account_id:
                        bank.save(update_fields=['debit_account_id', 'credit_account_id'])
                        bank_count += 1
                        self.stdout.write(f"  - Updated Bank ID {bank.id}: Dr {bank.debit_account_id} | Cr {bank.credit_account_id}")

        self.stdout.write(self.style.SUCCESS(f"Successfully updated {bank_count} Bank records.\n"))

        self.stdout.write(self.style.SUCCESS("--- Starting backfill for Cash records ---"))
        
        # Find Cash records missing either debit or credit account IDs
        cashes = Cash.objects.filter(
            Q(debit_account_id__isnull=True) | Q(debit_account_id='') |
            Q(credit_account_id__isnull=True) | Q(credit_account_id='')
        )
        
        cash_count = 0
        with transaction.atomic():
            for cash in cashes:
                je = cash.journal_entries.first()
                if je and je.lines.exists():
                    for line in je.lines.all():
                        if line.debit > 0:
                            cash.debit_account_id = line.account.account_id
                        if line.credit > 0:
                            cash.credit_account_id = line.account.account_id
                            
                    if cash.debit_account_id or cash.credit_account_id:
                        cash.save(update_fields=['debit_account_id', 'credit_account_id'])
                        cash_count += 1
                        self.stdout.write(f"  - Updated Cash ID {cash.id}: Dr {cash.debit_account_id} | Cr {cash.credit_account_id}")

        self.stdout.write(self.style.SUCCESS(f"Successfully updated {cash_count} Cash records."))