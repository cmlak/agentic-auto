import django_filters
from django import forms
from .models import Asset, DepreciationEntry, AssetDisposal
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column

class AssetFilter(django_filters.FilterSet):
    asset_code = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        field_name='asset_code',
        to_field_name='asset_code',
        label='Asset Code',
        empty_label='All Asset Codes'
    )
    asset_type = django_filters.ChoiceFilter(choices=Asset.ASSET_TYPES, empty_label='All Types')
    status = django_filters.ChoiceFilter(choices=Asset.STATUS, empty_label='All Statuses')

    class Meta:
        model = Asset
        fields = ['asset_code', 'asset_type', 'status']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters['asset_code'].field.label_from_instance = lambda obj: obj.asset_code
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            Row(
                Column('asset_code', css_class='form-group col-md-4 mb-0'),
                Column('asset_type', css_class='form-group col-md-4 mb-0'),
                Column('status', css_class='form-group col-md-4 mb-0'),
            )
        )

class DepreciationEntryFilter(django_filters.FilterSet):
    asset_code = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        field_name='asset__asset_code',
        to_field_name='asset_code',
        label='Asset Code',
        empty_label='All Asset Codes'
    )
    start_date = django_filters.DateFilter(
        field_name='date',
        lookup_expr='gte',
        label='Start Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    end_date = django_filters.DateFilter(
        field_name='date',
        lookup_expr='lte',
        label='End Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    class Meta:
        model = DepreciationEntry
        fields = ['asset_code', 'start_date', 'end_date']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters['asset_code'].field.label_from_instance = lambda obj: obj.asset_code
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            Row(
                Column('asset_code', css_class='form-group col-md-4 mb-0'),
                Column('start_date', css_class='form-group col-md-4 mb-0'),
                Column('end_date', css_class='form-group col-md-4 mb-0'),
            )
        )

class AssetDisposalFilter(django_filters.FilterSet):
    asset_code = django_filters.ModelChoiceFilter(
        queryset=Asset.objects.all(),
        field_name='asset__asset_code',
        to_field_name='asset_code',
        label='Asset Code',
        empty_label='All Asset Codes'
    )
    start_date = django_filters.DateFilter(
        field_name='disposal_date',
        lookup_expr='gte',
        label='Start Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )
    end_date = django_filters.DateFilter(
        field_name='disposal_date',
        lookup_expr='lte',
        label='End Date',
        widget=forms.DateInput(attrs={'type': 'date'})
    )

    class Meta:
        model = AssetDisposal
        fields = ['asset_code', 'start_date', 'end_date']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters['asset_code'].field.label_from_instance = lambda obj: obj.asset_code
        self.form.helper = FormHelper()
        self.form.helper.form_tag = False
        self.form.helper.layout = Layout(
            Row(
                Column('asset_code', css_class='form-group col-md-4 mb-0'),
                Column('start_date', css_class='form-group col-md-4 mb-0'),
                Column('end_date', css_class='form-group col-md-4 mb-0'),
            )
        )