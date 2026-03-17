from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from account.models import JournalEntry, JournalLine, Account

class Command(BaseCommand):
    help = 'Finds Journal Entries with missing lines and attempts to recreate them from the source document.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--je_ids',
            nargs='+',
            type=int,
            help='A list of specific Journal Entry IDs to process (e.g., 61 62 63).',
        )

    def handle(self, *args, **options):
        je_ids_to_process = options['je_ids'] or range(61, 69)
        self.stdout.write(self.style.SUCCESS(f"--- Starting script for JEs: {list(je_ids_to_process)} ---"))

        for je_id in je_ids_to_process:
            try:
                je = JournalEntry.objects.get(id=je_id)
                self.stdout.write(f"\nChecking Journal Entry ID: {je.id} ({je.date})")

                if je.lines.exists():
                    self.stdout.write(self.style.WARNING(f"  - OK: Journal lines already exist ({je.lines.count()} lines). Skipping."))
                    continue

                self.stdout.write(self.style.WARNING(f"  - MISSING: No journal lines found. Source is '{je.source_type}'."))

                if je.source_type == "Purchase":
                    self.create_lines_for_purchase(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
                elif je.source_type in ["Bank", "Cash Book"]:
                    self.stdout.write(self.style.ERROR(f"  - FAILED: Cannot auto-create lines for '{je.source_type}'."))
                    self.stdout.write("    Reason: The Debit/Credit account IDs are not stored on the source Bank/Cash record.")
                else:
                    self.stdout.write(f"  - SKIPPED: Source type '{je.source_type}' is not supported by this script.")

            except JournalEntry.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"\n- Journal Entry with ID {je_id} not found. Skipping."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\n- An unexpected error occurred for JE {je_id}: {e}"))

        self.stdout.write(self.style.SUCCESS("\n--- Script finished ---"))

    def create_lines_for_purchase(self, je: JournalEntry):
        """Creates the debit and credit lines for a Purchase-based Journal Entry."""
        purchase = je.purchase
        if not purchase:
            raise CommandError(f"JE {je.id} is not linked to a Purchase.")

        self.stdout.write(f"  - Processing Purchase ID {purchase.id}...")

        with transaction.atomic():
            total_amount = float(purchase.total_usd or 0.0)
            vat_amount = float(purchase.vat_usd or 0.0)
            net_amount = round(total_amount - vat_amount, 2)

            debit_acct_id = str(purchase.account_id) if purchase.account_id else '725080'
            credit_acct_id = str(purchase.credit_account_id) if purchase.credit_account_id else '200000'

            # CREDIT: Trade Payable
            if total_amount > 0:
                ap_account, _ = Account.objects.get_or_create(
                    client_id=je.client_id, account_id=credit_acct_id,
                    defaults={'name': 'Trade Payable - USD', 'account_type': 'Liability'}
                )
                JournalLine.objects.create(journal_entry=je, account=ap_account, description=f"Payable - {purchase.company}", credit=total_amount)
                self.stdout.write(f"    - Created CR line for {total_amount} to {credit_acct_id}")

            # DEBIT: VAT Input
            if vat_amount > 0:
                vat_account, _ = Account.objects.get_or_create(
                    client_id=je.client_id, account_id='115010',
                    defaults={'name': 'VAT input 进项增值税', 'account_type': 'Asset'}
                )
                JournalLine.objects.create(journal_entry=je, account=vat_account, description="Input VAT", debit=vat_amount)
                self.stdout.write(f"    - Created DR line for VAT {vat_amount} to 115010")

            # DEBIT: Expense Account
            if net_amount > 0:
                exp_account, _ = Account.objects.get_or_create(
                    client_id=je.client_id, account_id=debit_acct_id,
                    defaults={'name': 'Operating Expense', 'account_type': 'Expense'}
                )
                JournalLine.objects.create(
                    journal_entry=je, account=exp_account,
                    description=purchase.description_en or purchase.description or "Expense",
                    debit=net_amount
                )
                self.stdout.write(f"    - Created DR line for {net_amount} to {debit_acct_id}")