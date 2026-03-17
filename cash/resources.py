from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Bank, Cash
from tools.models import Client, Vendor, Purchase

class BankResource(resources.ModelResource):
    client = fields.Field(
        column_name='Client',
        attribute='client',
        widget=ForeignKeyWidget(Client, field='name')
    )
    matched_purchase = fields.Field(
        column_name='Matched Purchase Invoice',
        attribute='matched_purchase',
        widget=ForeignKeyWidget(Purchase, field='invoice_no')
    )
    debit_account = fields.Field(column_name='Debit Account')
    credit_account = fields.Field(column_name='Credit Account')

    def __init__(self, client_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client_id = client_id

    def get_queryset(self):
        qs = super().get_queryset()
        if self.client_id:
            qs = qs.filter(client_id=self.client_id)
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
            'id', 'client', 'batch', 'sys_id', 'date', 'bank_ref_id', 
            'trans_type', 'counterparty', 'purpose', 'remark', 'raw_remark', 
            'debit', 'credit', 'balance', 'debit_account', 'credit_account', 'matched_purchase', 'instruction', 'created_at',
        )
        export_order = fields

class CashResource(resources.ModelResource):
    client = fields.Field(
        column_name='Client',
        attribute='client',
        widget=ForeignKeyWidget(Client, field='name')
    )
    vendor = fields.Field(
        column_name='Vendor',
        attribute='vendor',
        widget=ForeignKeyWidget(Vendor, field='name')
    )
    matched_purchase = fields.Field(
        column_name='Matched Purchase Invoice',
        attribute='matched_purchase',
        widget=ForeignKeyWidget(Purchase, field='invoice_no')
    )
    debit_account = fields.Field(column_name='Debit Account')
    credit_account = fields.Field(column_name='Credit Account')

    def __init__(self, client_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client_id = client_id

    def get_queryset(self):
        qs = super().get_queryset()
        if self.client_id:
            qs = qs.filter(client_id=self.client_id)
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
            'id', 'client', 'batch', 'date', 'voucher_no', 'description', 
            'vendor', 'invoice_no', 'debit', 'credit', 'balance', 
            'debit_account', 'credit_account', 'matched_purchase', 'instruction', 'note',
        )
        export_order = fields