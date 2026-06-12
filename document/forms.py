from django import forms
from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout, Submit, Column, Row
from django.forms import modelformset_factory
from .models import DraftKnowledgeRule

class FinancialReportUploadForm(forms.Form):
    title = forms.CharField(max_length=255, help_text="e.g., Prakas No. 012 on WHT")
    source_url = forms.URLField(required=False, help_text="Optional link to the original source.")
    date_issued = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    document_pdf = forms.FileField(
        label="Financial Report (PDF)",
        widget=forms.FileInput(attrs={'accept': 'application/pdf'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_method = 'POST'
        self.helper.attrs = {'enctype': 'multipart/form-data'}
        self.helper.layout = Layout(
            Row(
                Column('title', css_class='form-group col-md-6 mb-0'),
                Column('date_issued', css_class='form-group col-md-6 mb-0'),
            ),
            Row(
                Column('source_url', css_class='form-group col-md-12 mb-0'),
            ),
            Row(
                Column('document_pdf', css_class='form-group col-md-12 mb-0'),
            ),
            Submit('submit', 'Upload and Extract Rules', css_class='btn btn-primary mt-3')
        )

class DraftKnowledgeRuleForm(forms.ModelForm):
    class Meta:
        model = DraftKnowledgeRule
        fields = ['proposed_agent_scope', 'proposed_title', 'proposed_condition', 'proposed_action_or_fact', 'proposed_tags', 'status']
        widgets = {
            'proposed_condition': forms.Textarea(attrs={'rows': 3}),
            'proposed_action_or_fact': forms.Textarea(attrs={'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False  # Avoid rendering individual <form> tags in a formset
        self.helper.disable_csrf = True
        
        # Set labels for better readability in the card layout
        self.fields['proposed_agent_scope'].label = 'Agent Scope'
        self.fields['proposed_title'].label = 'Title'
        self.fields['proposed_condition'].label = 'Condition'
        self.fields['proposed_action_or_fact'].label = 'Action / Fact'
        self.fields['proposed_tags'].label = 'Tags'
        self.fields['status'].label = 'Status'

        # Add tooltips on hover with full text
        for field_name, field in self.fields.items():
            if self.instance and hasattr(self.instance, field_name):
                val = getattr(self.instance, field_name)
                if val:
                    field.widget.attrs['title'] = str(val)

        # Redesign layout: Condition and Action/Fact are isolated into their own row to provide enough space
        self.helper.layout = Layout(
            Row(
                Column('proposed_agent_scope', css_class='form-group col-md-3 mb-2'),
                Column('proposed_title', css_class='form-group col-md-5 mb-2'),
                Column('proposed_tags', css_class='form-group col-md-4 mb-2'),
            ),
            Row(
                Column('proposed_condition', css_class='form-group col-md-6 mb-2'),
                Column('proposed_action_or_fact', css_class='form-group col-md-6 mb-2'),
            ),
            Row(
                Column('status', css_class='form-group col-md-4 mb-0'),
                Column('DELETE', css_class='form-group col-md-4 mb-0 d-flex align-items-center mt-4'),
            )
        )

DraftKnowledgeRuleFormSet = modelformset_factory(
    DraftKnowledgeRule,
    form=DraftKnowledgeRuleForm,
    extra=0,
    can_delete=True
)
