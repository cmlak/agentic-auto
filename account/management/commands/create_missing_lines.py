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
                elif je.source_type == "Sale":
                    self.create_lines_for_sale(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
                elif je.source_type == "Journal Voucher":
                    self.create_lines_for_journal_voucher(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
                elif je.source_type == "Adjustment":
                    self.create_lines_for_adjustment(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
                elif je.source_type == "Bank":
                    self.create_lines_for_bank(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
                elif je.source_type in ["Cash Book", "Cash"]:
                    self.create_lines_for_cash(je)
                    self.stdout.write(self.style.SUCCESS(f"  - SUCCESS: Journal lines created for JE {je.id}."))
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
            
            description = purchase.description_en or purchase.description or f"Payable - {purchase.company}"

            # CREDIT: Trade Payable
            if total_amount > 0:
                ap_account, _ = Account.objects.get_or_create(
                    account_id=credit_acct_id,
                    defaults={'name': 'Trade Payable - USD', 'account_type': 'Liability'}
                )
                JournalLine.objects.create(journal_entry=je, account=ap_account, description=description, credit=total_amount)
                self.stdout.write(f"    - Created CR line for {total_amount} to {credit_acct_id}")

            # DEBIT: VAT Input
            if vat_amount > 0:
                vat_account, _ = Account.objects.get_or_create(
                    account_id='115010',
                    defaults={'name': 'VAT input 进项增值税', 'account_type': 'Asset'}
                )
                JournalLine.objects.create(journal_entry=je, account=vat_account, description=description, debit=vat_amount)
                self.stdout.write(f"    - Created DR line for VAT {vat_amount} to 115010")

            # DEBIT: Expense Account
            if net_amount > 0:
                exp_account, _ = Account.objects.get_or_create(
                    account_id=debit_acct_id,
                    defaults={'name': 'Operating Expense', 'account_type': 'Expense'}
                )
                JournalLine.objects.create(
                    journal_entry=je, account=exp_account,
                    description=description,
                    debit=net_amount
                )
                self.stdout.write(f"    - Created DR line for {net_amount} to {debit_acct_id}")

    def create_lines_for_sale(self, je: JournalEntry):
        """Creates the debit and credit lines for a Sale-based Journal Entry."""
        sale = je.sale
        if not sale:
            raise CommandError(f"JE {je.id} is not linked to a Sale.")

        self.stdout.write(f"  - Processing Sale ID {sale.id}...")

        with transaction.atomic():
            total_amount = float(sale.total_usd or 0.0)
            vat_amount = float(sale.vat_usd or 0.0)
            net_amount = round(total_amount - vat_amount, 2)

            debit_acct_id = str(sale.debit_account_id) if sale.debit_account_id else '120000'
            credit_acct_id = str(sale.credit_account_id) if sale.credit_account_id else '500000'
            
            description = sale.description or "Revenue"

            # DEBIT: Accounts Receivable
            if total_amount > 0:
                ar_account, _ = Account.objects.get_or_create(
                    account_id=debit_acct_id,
                    defaults={'name': 'Trade Receivable', 'account_type': 'Asset'}
                )
                JournalLine.objects.create(journal_entry=je, account=ar_account, description=description, debit=total_amount)
                self.stdout.write(f"    - Created DR line for {total_amount} to {debit_acct_id}")

            # CREDIT: VAT Output
            if vat_amount > 0:
                vat_account, _ = Account.objects.get_or_create(
                    account_id='210010',
                    defaults={'name': 'VAT Output', 'account_type': 'Liability'}
                )
                JournalLine.objects.create(journal_entry=je, account=vat_account, description=description, credit=vat_amount)
                self.stdout.write(f"    - Created CR line for VAT {vat_amount} to 210010")

            # CREDIT: Revenue Account
            if net_amount > 0:
                rev_account, _ = Account.objects.get_or_create(
                    account_id=credit_acct_id,
                    defaults={'name': 'Operating Revenue', 'account_type': 'Revenue'}
                )
                JournalLine.objects.create(
                    journal_entry=je, account=rev_account,
                    description=description,
                    credit=net_amount
                )
                self.stdout.write(f"    - Created CR line for {net_amount} to {credit_acct_id}")

    def create_lines_for_journal_voucher(self, je: JournalEntry):
        jv = je.journal_voucher
        if not jv:
            raise CommandError(f"JE {je.id} is not linked to a Journal Voucher.")

        self.stdout.write(f"  - Processing Journal Voucher ID {jv.id}...")

        with transaction.atomic():
            acct_id = str(jv.account_id) if jv.account_id else '100000'
            description = jv.description or "Journal Voucher"
            debit_val = float(jv.debit or 0.0)
            credit_val = float(jv.credit or 0.0)

            acct, _ = Account.objects.get_or_create(
                account_id=acct_id,
                defaults={'name': 'JV Default', 'account_type': 'Asset'}
            )

            if debit_val > 0 and credit_val > 0:
                JournalLine.objects.create(journal_entry=je, account=acct, description=description, debit=debit_val, credit=0.0)
                JournalLine.objects.create(journal_entry=je, account=acct, description=description, debit=0.0, credit=credit_val)
                self.stdout.write(f"    - Created DR line for {debit_val} and CR line for {credit_val} to {acct_id}")
            else:
                JournalLine.objects.create(journal_entry=je, account=acct, description=description, debit=debit_val, credit=credit_val)
                self.stdout.write(f"    - Created line with DR {debit_val} and CR {credit_val} to {acct_id}")

    def create_lines_for_adjustment(self, je: JournalEntry):
        adj = je.adjustment
        if not adj:
            raise CommandError(f"JE {je.id} is not linked to an Adjustment.")

        self.stdout.write(f"  - Processing Adjustment ID {adj.id}...")

        with transaction.atomic():
            description = adj.description or "Adjustment"
            debit_val = float(adj.debit or 0.0)
            credit_val = float(adj.credit or 0.0)

            if adj.debit_account_id:
                JournalLine.objects.create(journal_entry=je, account=adj.debit_account_id, description=description, debit=debit_val)
                self.stdout.write(f"    - Created DR line for {debit_val} to {adj.debit_account_id.account_id}")
            
            if adj.credit_account_id:
                JournalLine.objects.create(journal_entry=je, account=adj.credit_account_id, description=description, credit=credit_val)
                self.stdout.write(f"    - Created CR line for {credit_val} to {adj.credit_account_id.account_id}")

    def create_lines_for_bank(self, je: JournalEntry):
        bank = je.bank
        if not bank:
            raise CommandError(f"JE {je.id} is not linked to a Bank record.")

        self.stdout.write(f"  - Processing Bank ID {bank.id}...")

        with transaction.atomic():
            description = bank.remark or bank.purpose or "Bank Transaction"
            amount = float(bank.debit or 0.0) if float(bank.debit or 0.0) > 0 else float(bank.credit or 0.0)

            if amount > 0:
                if bank.debit_account_id:
                    dr_acct, _ = Account.objects.get_or_create(
                        account_id=str(bank.debit_account_id),
                        defaults={'name': 'Bank DR Default', 'account_type': 'Asset'}
                    )
                    JournalLine.objects.create(journal_entry=je, account=dr_acct, description=description, debit=amount)
                    self.stdout.write(f"    - Created DR line for {amount} to {bank.debit_account_id}")

                if bank.credit_account_id:
                    cr_acct, _ = Account.objects.get_or_create(
                        account_id=str(bank.credit_account_id),
                        defaults={'name': 'Bank CR Default', 'account_type': 'Asset'}
                    )
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, description=description, credit=amount)
                    self.stdout.write(f"    - Created CR line for {amount} to {bank.credit_account_id}")

    def create_lines_for_cash(self, je: JournalEntry):
        cash = je.cash
        if not cash:
            raise CommandError(f"JE {je.id} is not linked to a Cash record.")

        self.stdout.write(f"  - Processing Cash ID {cash.id}...")

        with transaction.atomic():
            description = cash.description or "Cash Transaction"
            amount = float(cash.debit or 0.0) if float(cash.debit or 0.0) > 0 else float(cash.credit or 0.0)

            if amount > 0:
                if cash.debit_account_id:
                    dr_acct, _ = Account.objects.get_or_create(
                        account_id=str(cash.debit_account_id),
                        defaults={'name': 'Cash DR Default', 'account_type': 'Asset'}
                    )
                    JournalLine.objects.create(journal_entry=je, account=dr_acct, description=description, debit=amount)
                    self.stdout.write(f"    - Created DR line for {amount} to {cash.debit_account_id}")

                if cash.credit_account_id:
                    cr_acct, _ = Account.objects.get_or_create(
                        account_id=str(cash.credit_account_id),
                        defaults={'name': 'Cash CR Default', 'account_type': 'Asset'}
                    )
                    JournalLine.objects.create(journal_entry=je, account=cr_acct, description=description, credit=amount)
                    self.stdout.write(f"    - Created CR line for {amount} to {cash.credit_account_id}")