import django_filters
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit
from .models import Purchase, Vendor # Adjust imports based on your app structure

class PurchaseFilter(django_filters.FilterSet):
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all(), # We will dynamically limit this in the view
        label='Vendor',
        empty_label='All Vendors'
    )

    start_date = django_filters.DateFilter(
        field_name="date",
        lookup_expr="gte",
        label="Date From",
        widget=forms.DateInput(attrs={"type": "date"})
    )

    end_date = django_filters.DateFilter(
        field_name="date",
        lookup_expr="lte",
        label="Date To",
        widget=forms.DateInput(attrs={"type": "date"})
    )

    @property
    def form(self):
        form = super().form
        form.helper = FormHelper()
        form.helper.layout = Layout(
            Row(
                Column('vendor', css_class='form-group col-md-3 mb-0'),
                Column('start_date', css_class='form-group col-md-3 mb-0'),
                Column('end_date', css_class='form-group col-md-3 mb-0'),
                css_class='form-row'
            ),
            Submit('submit', 'Filter', css_class='btn btn-primary mt-3'),
        )
        return form

    class Meta:
        model = Purchase
        fields = []