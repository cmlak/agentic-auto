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