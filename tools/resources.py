from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from .models import Purchase, Vendor, Adjustment
from account.models import Account
from sale.models import Customer

class FloatWidget(Widget):
    def render(self, value, obj=None):
        if not value: return 0.0
        try: return float(value)
        except (ValueError, TypeError): return 0.0

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

    unreg_usd = fields.Field(attribute='unreg_usd', column_name='unreg_usd', widget=FloatWidget())
    exempt_usd = fields.Field(attribute='exempt_usd', column_name='exempt_usd', widget=FloatWidget())
    vat_base_usd = fields.Field(attribute='vat_base_usd', column_name='vat_base_usd', widget=FloatWidget())
    vat_usd = fields.Field(attribute='vat_usd', column_name='vat_usd', widget=FloatWidget())
    total_usd = fields.Field(attribute='total_usd', column_name='total_usd', widget=FloatWidget())
    debit_amount_2 = fields.Field(attribute='debit_amount_2', column_name='debit_amount_2', widget=FloatWidget())
    debit_amount_3 = fields.Field(attribute='debit_amount_3', column_name='debit_amount_3', widget=FloatWidget())

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
            'credit_account_id', 'wht_account_id', 
            'debit_account_id_2', 'debit_amount_2', 'debit_desc_2',
            'debit_account_id_3', 'debit_amount_3', 'debit_desc_3',
            'debit_account', 'credit_account', 'instruction', 'page', 'created_at',
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

    debit = fields.Field(attribute='debit', column_name='debit', widget=FloatWidget())
    credit = fields.Field(attribute='credit', column_name='credit', widget=FloatWidget())

    class Meta:
        model = Adjustment
        fields = (
            'id', 'date', 'vendor', 'customer', 'debit_account', 'credit_account',
            'debit', 'credit', 'description', 'created_at',
        )
        export_order = fields
