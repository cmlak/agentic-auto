from import_export import resources
from import_export.fields import Field
from import_export.widgets import ForeignKeyWidget
from .models import Account
from tools.models import Client

class AccountResource(resources.ModelResource):
    client = Field(
        column_name='client_id',
        attribute='client',
        widget=ForeignKeyWidget(Client, 'id')
    )

    class Meta:
        model = Account
        fields = ('client', 'account_id', 'name', 'account_type')
        import_id_fields = ('client', 'account_id')
        skip_unchanged = True
        report_skipped = True

    def before_import_row(self, row, **kwargs):
        """
        Clean the account_id to ensure it's always treated as a string and 
        strips any trailing '.0' that Excel/tablib might inject when parsing 
        numeric IDs as floats. This prevents the system from failing to find 
        existing accounts and creating duplicates (e.g. '1000.0' instead of '1000').
        """
        if 'account_id' in row and row['account_id']:
            clean_id = str(row['account_id']).strip()
            if clean_id.endswith('.0'):
                clean_id = clean_id[:-2]
            row['account_id'] = clean_id

class TrialBalanceResource(resources.Resource):
    id = Field(attribute='id', column_name='Account ID')
    name = Field(attribute='name', column_name='Account Name')
    type = Field(attribute='type', column_name='Account Type')
    debit = Field(attribute='debit', column_name='Debit')
    credit = Field(attribute='credit', column_name='Credit')

    class Meta:
        export_order = ('id', 'name', 'type', 'debit', 'credit')

class ProfitAndLossResource(resources.Resource):
    category = Field(attribute='category', column_name='Category')
    account_id = Field(attribute='account_id', column_name='Account ID')
    account_name = Field(attribute='account_name', column_name='Account')
    total = Field(attribute='total', column_name='Total')

    class Meta:
        export_order = ('category', 'account_id', 'account_name', 'total')

class BalanceSheetResource(resources.Resource):
    category = Field(attribute='category', column_name='Category')
    account_id = Field(attribute='account_id', column_name='Account ID')
    account_name = Field(attribute='account_name', column_name='Account')
    balance = Field(attribute='balance', column_name='Balance')

    class Meta:
        export_order = ('category', 'account_id', 'account_name', 'balance')

class GeneralLedgerSummaryResource(resources.Resource):
    account_id = Field(attribute='account_id', column_name='Account ID')
    name = Field(attribute='name', column_name='Account Name')
    account_type = Field(attribute='account_type', column_name='Account Type')
    debit = Field(attribute='debit', column_name='Total Debit')
    credit = Field(attribute='credit', column_name='Total Credit')
    balance = Field(attribute='balance', column_name='Balance')

    class Meta:
        export_order = ('account_id', 'name', 'account_type', 'debit', 'credit', 'balance')

class AccountLedgerDetailResource(resources.Resource):
    date = Field(attribute='date', column_name='Date')
    description = Field(attribute='description', column_name='Description')
    source = Field(attribute='source', column_name='Source')
    debit = Field(attribute='debit', column_name='Debit')
    credit = Field(attribute='credit', column_name='Credit')
    balance = Field(attribute='balance', column_name='Running Balance')

    class Meta:
        export_order = ('date', 'description', 'source', 'debit', 'credit', 'balance')