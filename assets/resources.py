from import_export import resources
from .models import Asset

class AssetResource(resources.ModelResource):
    class Meta:
        model = Asset
        fields = ('id', 'asset_code', 'asset_type', 'status', 'purchase_cost', 'depreciation_start_date', 'depreciation_method', 'useful_life_months', 'salvage_value', 'asset_account', 'acc_dep_account', 'dep_expense_account')
        export_order = fields