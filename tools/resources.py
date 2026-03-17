from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget
from .models import Purchase, Vendor, Client

class PurchaseResource(resources.ModelResource):
    client = fields.Field(
        column_name='Client',
        attribute='client',
        widget=ForeignKeyWidget(Client, field='name')
    )

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

    # 1. Accept client_id parameter during initialization
    def __init__(self, client_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client_id = client_id

    # 2. Strictly filter the base queryset inside the resource
    def get_queryset(self):
        qs = super().get_queryset()
        if self.client_id:
            qs = qs.filter(client_id=self.client_id)
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
            'id', 'client', 'batch', 'date', 'invoice_no', 'company', 
            'vendor', 'vattin', 'description', 'description_en', 
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 
            'credit_account_id', 'wht_account_id', 'debit_account', 'credit_account', 'instruction', 'page', 'created_at',
        )
        export_order = fields
