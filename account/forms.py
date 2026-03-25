from django import forms
from tools.models import Client

class AccountImportForm(forms.Form):
    client = forms.ModelChoiceField(
        queryset=Client.objects.all(),
        label="Client",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    import_file = forms.FileField(
        label="Chart of Accounts File (CSV/Excel)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv, .xls, .xlsx'})
    )