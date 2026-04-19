import django_filters
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Row, Column, Submit, HTML
from django.db.models import Q
from .models import Bank, Cash
from tools.models import Vendor

class BankFilter(django_filters.FilterSet):
    BANK_CHOICES = [
        ('100200', 'ABA Bank - USD'),
        ('100210', 'ABA Bank - KHR'),
        ('100300', 'CANADIA - USD'),
        ('100310', 'CANADIA - KHR'),
    ]

    bank = django_filters.ChoiceFilter(
        label='Bank Account',
        choices=BANK_CHOICES,
        method='filter_bank',
        empty_label='All Banks',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all(),
        label='Vendor', empty_label='All Vendors',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    remark = django_filters.ChoiceFilter(
        label='Remark',
        empty_label='All Remarks',
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

    def filter_bank(self, queryset, name, value):
        return queryset.filter(Q(debit_account_id=value) | Q(credit_account_id=value))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.queryset is not None:
            remark_choices = list(
                self.queryset.exclude(remark__isnull=True).exclude(remark__exact='')
                .values_list('remark', 'remark').order_by('remark').distinct()
            )
            self.filters['remark'].extra['choices'] = remark_choices

    @property
    def form(self):
        form = super().form
        form.helper = FormHelper()
        form.helper.form_method = 'GET'
        form.helper.layout = Layout(
            Row(
                Column('bank', css_class='form-group col-md-3 mb-3'),
                Column('vendor', css_class='form-group col-md-3 mb-3'),
                Column('remark', css_class='form-group col-md-2 mb-3'),
                Column('start_date', css_class='form-group col-md-2 mb-3'),
                Column('end_date', css_class='form-group col-md-2 mb-3'),
                css_class='row'
            ),
            Row(
                Column(
                    Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'),
                    HTML('<a href="{% url \'cash:bank_list\' %}" class="btn btn-secondary px-4">Clear</a>'),
                    css_class='col-12 text-center mb-2'
                ),
                css_class='row'
            )
        )
        return form

    class Meta:
        model = Bank
        fields = []

class CashFilter(django_filters.FilterSet):
    vendor = django_filters.ModelChoiceFilter(
        queryset=Vendor.objects.all(), # Overridden dynamically in view
        label='Vendor', empty_label='All Vendors',
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
                Column(Submit('submit', 'Filter', css_class='btn btn-primary px-4 me-2'), HTML('<a href="{% url \'cash:cash_list\' %}" class="btn btn-secondary px-4">Clear</a>'), css_class='col-12 text-center mb-2'),
            )
        )
        return form

    class Meta:
        model = Cash
        fields = []