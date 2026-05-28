import django_filters
from django import forms
from .models import Asset

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