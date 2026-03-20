import django_filters
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Row, Column, HTML
from .models import JournalLine

class ReportFilter(django_filters.FilterSet):
    start_date = django_filters.DateFilter(
        field_name='journal_entry__date',
        lookup_expr='gte',
        label='Start Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    end_date = django_filters.DateFilter(
        field_name='journal_entry__date',
        lookup_expr='lte',
        label='End Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    class Meta:
        model = JournalLine
        fields = ['start_date', 'end_date']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            Row(
                Column('start_date', css_class='form-group col-md-4 mb-0'),
                Column('end_date', css_class='form-group col-md-4 mb-0'),
                css_class='justify-content-center'
            ),
            Row(
                Column(
                    Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'),
                    HTML('<a href="?" class="btn btn-outline-secondary px-4">Clear</a>'),
                    css_class='form-group col-md-12 text-center mt-3 mb-0'
                ),
                css_class='justify-content-center'
            )
        )

class BalanceSheetFilter(django_filters.FilterSet):
    end_date = django_filters.DateFilter(
        field_name='journal_entry__date',
        lookup_expr='lte',
        label='As of Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    class Meta:
        model = JournalLine
        fields = ['end_date']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            Row(
                Column('end_date', css_class='form-group col-md-4 mb-0'),
                css_class='justify-content-center'
            ),
            Row(
                Column(
                    Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'),
                    HTML('<a href="?" class="btn btn-outline-secondary px-4">Clear</a>'),
                    css_class='form-group col-md-12 text-center mt-3 mb-0'
                ),
                css_class='justify-content-center'
            )
        )