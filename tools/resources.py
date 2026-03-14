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

    # 1. Accept client_id parameter during initialization
    def __init__(self, client_id=None, **kwargs):
        super().__init__(**kwargs)
        self.client_id = client_id

    # 2. Strictly filter the base queryset inside the resource
    def get_queryset(self):
        qs = super().get_queryset()
        if self.client_id:
            return qs.filter(client_id=self.client_id)
        return qs

    class Meta:
        model = Purchase
        fields = (
            'id', 'client', 'batch', 'date', 'invoice_no', 'company', 
            'vendor', 'vattin', 'description', 'description_en', 
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 
            'credit_account_id', 'wht_account_id', 'instruction', 'page', 'created_at',
        )
        export_order = fields