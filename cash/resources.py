from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Bank, Cash
from tools.models import Vendor, Purchase

class FloatWidget(Widget):
    def render(self, value, obj=None):
        if not value: return 0.0
        try: return float(value)
        except (ValueError, TypeError): return 0.0

try:
    from sale.models import Customer, Sale
except ImportError:
    Customer, Sale = None, None

class BankResource(resources.ModelResource):
    vendor = fields.Field(
        column_name='Vendor',
        attribute='vendor',
        widget=ForeignKeyWidget(Vendor, field='name')
    )
    customer = fields.Field(
        column_name='Customer',
        attribute='customer',
        widget=ForeignKeyWidget(Customer, field='name') if Customer else None
    )
    matched_purchase = fields.Field(
        column_name='Matched Purchase Invoice',
        attribute='matched_purchase',
        widget=ForeignKeyWidget(Purchase, field='invoice_no')
    )
    matched_sale = fields.Field(
        column_name='Matched Sale Invoice',
        attribute='matched_sale',
        widget=ForeignKeyWidget(Sale, field='invoice_no') if Sale else None
    )
    debit_account = fields.Field(column_name='Debit Account')
    credit_account = fields.Field(column_name='Credit Account')

    debit = fields.Field(attribute='debit', column_name='debit', widget=FloatWidget())
    credit = fields.Field(attribute='credit', column_name='credit', widget=FloatWidget())
    balance = fields.Field(attribute='balance', column_name='balance', widget=FloatWidget())

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.prefetch_related('journal_entries__lines__account')

    def dehydrate_debit_account(self, bank):
        jes = list(bank.journal_entries.all())
        if jes:
            for line in list(jes[0].lines.all()):
                if line.debit > 0:
                    return f"{line.account.account_id} - {line.account.name}"
        return ""

    def dehydrate_credit_account(self, bank):
        jes = list(bank.journal_entries.all())
        if jes:
            for line in list(jes[0].lines.all()):
                if line.credit > 0:
                    return f"{line.account.account_id} - {line.account.name}"
        return ""

    class Meta:
        model = Bank
        fields = (
            'id', 'batch', 'sys_id', 'date', 'bank_ref_id',
            'trans_type', 'counterparty', 'vendor', 'customer', 'purpose', 'remark', 'raw_remark',
            'debit', 'credit', 'balance', 'debit_account', 'credit_account', 'matched_purchase', 'matched_sale', 'instruction', 'created_at',
        )
        export_order = fields

class CashResource(resources.ModelResource):
    vendor = fields.Field(
        column_name='Vendor',
        attribute='vendor',
        widget=ForeignKeyWidget(Vendor, field='name')
    )
    customer = fields.Field(
        column_name='Customer',
        attribute='customer',
        widget=ForeignKeyWidget(Customer, field='name') if Customer else None
    )
    matched_purchase = fields.Field(
        column_name='Matched Purchase Invoice',
        attribute='matched_purchase',
        widget=ForeignKeyWidget(Purchase, field='invoice_no')
    )
    matched_sale = fields.Field(
        column_name='Matched Sale Invoice',
        attribute='matched_sale',
        widget=ForeignKeyWidget(Sale, field='invoice_no') if Sale else None
    )
    debit_account = fields.Field(column_name='Debit Account')
    credit_account = fields.Field(column_name='Credit Account')

    debit = fields.Field(attribute='debit', column_name='debit', widget=FloatWidget())
    credit = fields.Field(attribute='credit', column_name='credit', widget=FloatWidget())
    balance = fields.Field(attribute='balance', column_name='balance', widget=FloatWidget())

    def get_queryset(self):
        qs = super().get_queryset()
        return qs.prefetch_related('journal_entries__lines__account')

    def dehydrate_debit_account(self, cash):
        jes = list(cash.journal_entries.all())
        if jes:
            for line in list(jes[0].lines.all()):
                if line.debit > 0:
                    return f"{line.account.account_id} - {line.account.name}"
        return ""

    def dehydrate_credit_account(self, cash):
        jes = list(cash.journal_entries.all())
        if jes:
            for line in list(jes[0].lines.all()):
                if line.credit > 0:
                    return f"{line.account.account_id} - {line.account.name}"
        return ""

    class Meta:
        model = Cash
        fields = (
            'id', 'batch', 'date', 'voucher_no', 'description',
            'vendor', 'customer', 'invoice_no', 'debit', 'credit', 'balance',
            'debit_account', 'credit_account', 'matched_purchase', 'matched_sale', 'instruction', 'note',
        )
        export_order = fields