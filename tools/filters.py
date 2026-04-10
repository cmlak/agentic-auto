import django_filters
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, HTML
from .models import Purchase, Vendor, JournalVoucher

class PurchaseFilter(django_filters.FilterSet):
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all().order_by('vendor_id'), # We will dynamically limit this in the view
        label='Vendor',
        empty_label='All Vendors',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    description_en = django_filters.ChoiceFilter(
        label='Description',
        empty_label='All Descriptions',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    payment_status = django_filters.ChoiceFilter(
        choices=Purchase.PAYMENT_STATUS_CHOICES,
        label='Payment Status',
        empty_label='All Statuses',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    start_date = django_filters.DateFilter(
        field_name="date",
        lookup_expr="gte",
        label="Date From",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

    end_date = django_filters.DateFilter(
        field_name="date",
        lookup_expr="lte",
        label="Date To",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.queryset is not None:
            # Dynamically populate description choices based on available records
            desc_choices = list(
                self.queryset.exclude(description_en__isnull=True)
                .exclude(description_en__exact='')
                .values_list('description_en', 'description_en')
                .order_by('description_en')
                .distinct()
            )
            self.filters['description_en'].extra['choices'] = desc_choices

    @property
    def form(self):
        form = super().form
        form.helper = FormHelper()
        form.helper.form_method = 'GET'
        form.helper.layout = Layout(
            Row(
                Column('vendor', css_class='form-group col-md-2 mb-3'),
                Column('description_en', css_class='form-group col-md-3 mb-3'),
                Column('payment_status', css_class='form-group col-md-3 mb-3'),
                Column('start_date', css_class='form-group col-md-2 mb-3'),
                Column('end_date', css_class='form-group col-md-2 mb-3'),
                css_class='row'
            ),
            Row(
                Column(
                    Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'),
                    HTML('<a href="{% url \'tools:purchase_list\' %}" class="btn btn-secondary px-4">Clear</a>'),
                    css_class='col-12 text-center mb-2'
                ),
                css_class='row'
            )
        )
        return form

    class Meta:
        model = Purchase
        fields = []

class JournalVoucherFilter(django_filters.FilterSet):
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all().order_by('vendor_id'),
        label='Vendor',
        empty_label='All Vendors',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    start_date = django_filters.DateFilter(
        field_name="date", lookup_expr="gte", label="Date From",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )
    end_date = django_filters.DateFilter(
        field_name="date", lookup_expr="lte", label="Date To",
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"})
    )

    @property
    def form(self):
        form = super().form
        form.helper = FormHelper()
        form.helper.form_method = 'GET'
        form.helper.layout = Layout(
            Row(
                Column('vendor', css_class='form-group col-md-4 mb-3'),
                Column('start_date', css_class='form-group col-md-4 mb-3'),
                Column('end_date', css_class='form-group col-md-4 mb-3'),
                css_class='row'
            ),
            Row(
                Column(Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'), HTML('<a href="{% url \'tools:journal_voucher_list\' %}" class="btn btn-secondary px-4">Clear</a>'), css_class='col-12 text-center mb-2'),
            )
        )
        return form

    class Meta:
        model = JournalVoucher
        fields = []