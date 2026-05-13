from django import forms

class AccountImportForm(forms.Form):
    import_file = forms.FileField(
        label="Chart of Accounts File (CSV/Excel)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.csv, .xls, .xlsx'})
    )