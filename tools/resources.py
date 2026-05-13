from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Purchase, Vendor, Adjustment
from account.models import Account
from sale.models import Customer

class PurchaseResource(resources.ModelResource):

    vendor = fields.Field(
        column_name='Vendor (DB)',
        attribute='vendor',
        widget=ForeignKeyWidget(Vendor, field='name')
    )
    
    batch = fields.Field(
        column_name='Batch',
        attribute='batch'
    )
    debit_account = fields.Field(column_name='Debit Account')
    credit_account = fields.Field(column_name='Credit Account')

    # Strictly filter the base queryset inside the resource
    def get_queryset(self):
        qs = super().get_queryset()
        return qs.prefetch_related('journal_entries__lines__account')

    def dehydrate_debit_account(self, purchase):
        jes = list(purchase.journal_entries.all())
        if jes:
            debits = [f"{line.account.account_id} - {line.account.name}" for line in jes[0].lines.all() if line.debit > 0]
            return ", ".join(debits)
        return ""

    def dehydrate_credit_account(self, purchase):
        jes = list(purchase.journal_entries.all())
        if jes:
            credits = [f"{line.account.account_id} - {line.account.name}" for line in jes[0].lines.all() if line.credit > 0]
            return ", ".join(credits)
        return ""

    class Meta:
        model = Purchase
        fields = (
            'id', 'batch', 'date', 'invoice_no', 'company', 
            'vendor', 'vattin', 'description', 'description_en', 
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 
            'credit_account_id', 'wht_account_id', 'debit_account', 'credit_account', 'instruction', 'page', 'created_at',
        )
        export_order = fields

class AdjustmentResource(resources.ModelResource):
    vendor = fields.Field(
        column_name='Vendor',
        attribute='vendor',
        widget=ForeignKeyWidget(Vendor, field='name')
    )
    customer = fields.Field(
        column_name='Customer',
        attribute='customer',
        widget=ForeignKeyWidget(Customer, field='name')
    )
    debit_account = fields.Field(
        column_name='Debit Account',
        attribute='debit_account_id',
        widget=ForeignKeyWidget(Account, field='name')
    )
    credit_account = fields.Field(
        column_name='Credit Account',
        attribute='credit_account_id',
        widget=ForeignKeyWidget(Account, field='name')
    )

    class Meta:
        model = Adjustment
        fields = (
            'id', 'date', 'vendor', 'customer', 'debit_account', 'credit_account',
            'debit', 'credit', 'description', 'created_at',
        )
        export_order = fields
