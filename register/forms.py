from register.models import Profile
from django.contrib.auth.models import User
from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Layout, Row, Column

DEPARTMENT_CHOICES = (
        ('Audit', 'Audit'),
        ('Tax', 'Tax'),
        ('Accounting', 'Accounting'),
        ('Business Advisory', 'Business Advisory'),
        ('General Administration', 'General Administration'),
        ('Finance', 'Finance'),
        ('IT', 'IT'),
    )

CATEGORY_CHOICES = (
        ('External Audit - Preliminary', 'External Audit - Preliminary'),
        ('Monthly Tax Declaration', 'Monthly Tax Declaration'),
        ('Annual Tax Declaration', 'Annual Tax Declaration'),
    )

MONTH_CHOICES = (
        ('January', 'January'),
        ('February', 'February'),
        ('March', 'March'),
        ('April', 'April'),
        ('May', 'May'),
        ('June', 'June'),
        ('July', 'July'),
        ('August', 'August'),
        ('September', 'September'),
        ('October', 'October'),
        ('November', 'November'),
        ('December', 'December'),
    )

YEAR_CHOICES = (
        ('2023', '2023'),
        ('2024', '2024'),
        ('2025', '2025'),
    )

class EmptyLabelSelect(forms.Select):
    def __init__(self, attrs=None, choices=(), empty_label=None):
        super().__init__(attrs, choices)
        if empty_label is not None:
            self.choices = [('', empty_label)] + list(self.choices)

class ProfileCreateForm(forms.ModelForm):

    department = forms.ChoiceField(label='Department', choices=DEPARTMENT_CHOICES, widget=EmptyLabelSelect(empty_label='---------'), required=False)

    class Meta:
        model = Profile
        fields = (
            'department',
        )

    