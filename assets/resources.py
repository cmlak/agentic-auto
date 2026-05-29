from import_export import resources
from import_export.fields import Field
from import_export.widgets import Widget
from .models import Asset, DepreciationEntry, AssetDisposal

class FloatWidget(Widget):
    def render(self, value, obj=None):
        if not value: return 0.0
        try: return float(value)
        except (ValueError, TypeError): return 0.0

class IntWidget(Widget):
    def render(self, value, obj=None):
        if not value: return 0
        try: return int(value)
        except (ValueError, TypeError): return 0

class AssetResource(resources.ModelResource):
    asset_type = Field(attribute='get_asset_type_display', column_name='Asset Type')
    status = Field(attribute='get_status_display', column_name='Status')
    depreciation_method = Field(attribute='get_depreciation_method_display', column_name='Depreciation Method')
    asset_account = Field(attribute='asset_account__name', column_name='Asset Account')
    acc_dep_account = Field(attribute='acc_dep_account__name', column_name='Accumulated Dep. Account')
    dep_expense_account = Field(attribute='dep_expense_account__name', column_name='Depreciation Expense Account')

    purchase_cost = Field(attribute='purchase_cost', column_name='Purchase Cost', widget=FloatWidget())
    salvage_value = Field(attribute='salvage_value', column_name='Salvage Value', widget=FloatWidget())
    useful_life_months = Field(attribute='useful_life_months', column_name='Useful Life (Months)', widget=IntWidget())

    class Meta:
        model = Asset
        fields = ('id', 'asset_code', 'asset_type', 'status', 'purchase_cost', 'depreciation_start_date', 'depreciation_method', 'useful_life_months', 'salvage_value', 'asset_account', 'acc_dep_account', 'dep_expense_account')
        export_order = fields

class DepreciationEntryResource(resources.ModelResource):
    asset_code = Field(attribute='asset__asset_code', column_name='Asset Code')
    amount = Field(attribute='amount', column_name='Amount', widget=FloatWidget())

    class Meta:
        model = DepreciationEntry
        fields = ('id', 'asset_code', 'date', 'amount', 'created_at')
        export_order = fields

class AssetDisposalResource(resources.ModelResource):
    asset_code = Field(attribute='asset__asset_code', column_name='Asset Code')
    disposal_income_account = Field(attribute='disposal_income_account__name', column_name='Disposal Income Account')
    proceeds = Field(attribute='proceeds', column_name='Proceeds', widget=FloatWidget())
    net_book_value_at_disposal = Field(attribute='net_book_value_at_disposal', column_name='Net Book Value at Disposal', widget=FloatWidget())
    gain_loss_amount = Field(attribute='gain_loss_amount', column_name='Gain/Loss Amount', widget=FloatWidget())

    class Meta:
        model = AssetDisposal
        fields = ('id', 'asset_code', 'disposal_date', 'proceeds', 'disposal_income_account', 'net_book_value_at_disposal', 'gain_loss_amount')
        export_order = fields

class DepreciationScheduleResource(resources.Resource):
    period = Field(attribute='period', column_name='Period', widget=IntWidget())
    date = Field(attribute='date', column_name='Date')
    depreciation_expense = Field(attribute='depreciation_expense', column_name='Depreciation Expense', widget=FloatWidget())
    accumulated_depreciation = Field(attribute='accumulated_depreciation', column_name='Accumulated Depreciation', widget=FloatWidget())
    net_book_value = Field(attribute='net_book_value', column_name='Net Book Value', widget=FloatWidget())

    class Meta:
        export_order = ('period', 'date', 'depreciation_expense', 'accumulated_depreciation', 'net_book_value')