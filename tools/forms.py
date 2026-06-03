from django import forms
from django.forms import formset_factory, BaseFormSet
from django.core.exceptions import ValidationError
from crispy_forms.helper import FormHelper
from django.urls import reverse_lazy
from datetime import date
from crispy_forms.layout import Layout, Row, Column, Field, Submit, HTML, Div
from .models import Purchase, Vendor, Old, JournalVoucher, AICostLog, Adjustment
from account.models import Account
from sale.models import Customer

# ====================================================================
# 1. INITIAL UPLOAD FORMS
# ====================================================================

class BatchUploadForm(forms.Form):
    invoice_pdf = forms.FileField(
        label="Upload Invoice Batch (PDF)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'})
    )
    batch_name = forms.CharField(
        label="Batch Name", max_length=255, required=True,
        help_text="e.g., CCKT Batch 1 - 10 March 2026",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    ai_prompt = forms.CharField(
        label="Custom AI Instructions (Optional)",
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'e.g., Extract sequences starting from 20260305...'}),
        required=False
    )

class PurchaseReviewForm(forms.ModelForm):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    vendor_choice = forms.ChoiceField(label="Matched Vendor DB", required=False, widget=forms.Select(attrs={'class': 'form-select fw-bold'}))
    
    # --- DEBITS ---
    account_id = forms.ChoiceField(
        label="Main Debit Account (Current Month)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    # 💡 NEW: Split Accrual Dropdowns
    debit_account_id_2 = forms.ChoiceField(
        label="Accrual Clearing Account 2 (Dr)", required=False,
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    debit_account_id_3 = forms.ChoiceField(
        label="Accrual Clearing Account 3 (Dr)", required=False,
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    vat_account_id = forms.ChoiceField(
        label="VAT Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    wht_debit_account_id = forms.ChoiceField(
        label="WHT Expense (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    
    # --- CREDITS ---
    credit_account_id = forms.ChoiceField(
        label="Main Credit Account (Payable)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Payable (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )
    
    # --- VISUAL AMOUNT FIELDS (UNLOCKED FOR EDITING) ---
    net_amount = forms.CharField(
        label="Net Expense Amount (Main Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end fw-bold text-primary'})
    )
    wht_amount_dr = forms.CharField(
        label="WHT Amount (Dr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )
    wht_amount_cr = forms.CharField(
        label="WHT Amount (Cr)", required=False, 
        widget=forms.TextInput(attrs={'class': 'number-format text-end', 'placeholder': 'Optional override'})
    )

    class Meta:
        model = Purchase
        fields = [
            'batch', 'date', 'invoice_no', 'company', 'vendor', 'vattin', 
            'account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id',
            'debit_account_id_2', 'debit_amount_2', 'debit_desc_2',  # 💡 NEW Accrual fields
            'debit_account_id_3', 'debit_amount_3', 'debit_desc_3',  # 💡 NEW Accrual fields
            'description', 'description_en', 'instruction',
            'unreg_usd', 'exempt_usd',
            'vat_base_usd', 'vat_usd', 'total_usd', 'page', 'payment_status'
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'invoice_no': forms.TextInput(attrs={'class': 'form-control fw-bold'}),
            'vattin': forms.TextInput(attrs={'class': 'form-control'}),
            'company': forms.TextInput(attrs={'class': 'form-control fw-bold'}),
            'page': forms.NumberInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'rows': 1, 'class': 'form-control auto-expand'}),
            'description_en': forms.Textarea(attrs={'rows': 1, 'class': 'form-control auto-expand'}),
            'instruction': forms.Textarea(attrs={'rows': 1, 'placeholder': 'Optional AI or manual notes...', 'class': 'form-control auto-expand text-muted'}), 
            'vendor': forms.HiddenInput(), 
            # 💡 NEW: Accrual Field Formatting Widgets
            'debit_amount_2': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary fw-bold'}),
            'debit_desc_2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description for accrual line 2'}),
            'debit_amount_3': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary fw-bold'}),
            'debit_desc_3': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description for accrual line 3'}),
            'unreg_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary fw-bold'}),
            'total_usd': forms.TextInput(attrs={'class': 'form-control number-format text-end text-danger fw-bold'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
        }
        labels = {
            'debit_amount_2': 'Accrual Amount 2',
            'debit_desc_2': 'Accrual Memo 2',
            'debit_amount_3': 'Accrual Amount 3',
            'debit_desc_3': 'Accrual Memo 3',
            'unreg_usd': 'Unregistered (WHT Base)',
            'exempt_usd': 'Exempt (No VAT)',
            'vat_base_usd': 'VAT Base Amount',
            'vat_usd': 'VAT Amount (Dr)',
            'total_usd': 'Gross Payable (Cr)',
            'payment_status': 'Payment Status',
        }

    def __init__(self, *args, **kwargs):
        dynamic_choices = kwargs.pop('dynamic_choices', None)
        account_choices = kwargs.pop('account_choices', None) 
        super().__init__(*args, **kwargs)
        
        # 1. Populate dynamic dropdowns (With updated accrual field paths)
        if dynamic_choices:
            self.fields['vendor_choice'].choices = dynamic_choices
        if self.initial.get('vendor_choice'):
            self.fields['vendor_choice'].initial = self.initial.get('vendor_choice')

        if account_choices:
            account_fields = [
                'account_id', 'vat_account_id', 'wht_debit_account_id', 
                'credit_account_id', 'wht_account_id', 'debit_account_id_2', 'debit_account_id_3'
            ]
            for field in account_fields:
                self.fields[field].choices = account_choices
                if self.initial.get(field):
                    self.fields[field].initial = self.initial.get(field)

        # 2. Numbering the formset rows
        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        self.fields['batch'].disabled = True

        # 3. Calculate "Main Net Amount" (Gross - VAT - Accrual 2 - Accrual 3)
        # This isolates the literal current month expense cleanly
        t_val = float(self.initial.get('total_usd') or 0)
        v_val = float(self.initial.get('vat_usd') or 0)
        a2_val = float(self.initial.get('debit_amount_2') or 0)
        a3_val = float(self.initial.get('debit_amount_3') or 0)
        
        if not self.initial.get('net_amount'):
            calculated_net = t_val - v_val - a2_val - a3_val
            self.fields['net_amount'].initial = f"{calculated_net:,.2f}"

        # ==========================================================
        # 4. CRISPY FORMS DYNAMIC UI LAYOUT
        # ==========================================================
        # Determine visibility based on initial data
        a2_visible = bool(self.initial.get('debit_account_id_2') or a2_val != 0)
        a3_visible = bool(self.initial.get('debit_account_id_3') or a3_val != 0)
        vat_visible = bool(self.initial.get('vat_account_id') or v_val != 0)
        wht_dr_visible = bool(self.initial.get('wht_debit_account_id') or self.initial.get('wht_amount_dr'))
        wht_cr_visible = bool(self.initial.get('wht_account_id') or self.initial.get('wht_amount_cr'))
        
        # If form is bound (e.g., during validation errors), keep rows visible if user filled them
        if self.is_bound:
            a2_visible = a2_visible or bool(self.data.get(self.add_prefix('debit_account_id_2')) or self.data.get(self.add_prefix('debit_amount_2')))
            a3_visible = a3_visible or bool(self.data.get(self.add_prefix('debit_account_id_3')) or self.data.get(self.add_prefix('debit_amount_3')))
            vat_visible = vat_visible or bool(self.data.get(self.add_prefix('vat_account_id')) or self.data.get(self.add_prefix('vat_usd')))
            wht_dr_visible = wht_dr_visible or bool(self.data.get(self.add_prefix('wht_debit_account_id')) or self.data.get(self.add_prefix('wht_amount_dr')))
            wht_cr_visible = wht_cr_visible or bool(self.data.get(self.add_prefix('wht_account_id')) or self.data.get(self.add_prefix('wht_amount_cr')))

        account_rows = []

        # Row 1: Main Debit (Current Month Expense portion)
        account_rows.append(Row(
            Column('account_id', css_class='form-group col-md-9'),
            Column('net_amount', css_class='form-group col-md-3'),
        ))

        # 💡 Secondary Accrual Debit (Dynamic rendering)
        account_rows.append(Row(
            Column('debit_account_id_2', css_class='form-group col-md-5'),
            Column('debit_desc_2', css_class='form-group col-md-4'),
            Column('debit_amount_2', css_class='form-group col-md-3'),
            css_class=f"accrual-fields {'d-none' if not a2_visible else ''}"
        ))

        # 💡 Tertiary Accrual Debit (Dynamic rendering)
        account_rows.append(Row(
            Column('debit_account_id_3', css_class='form-group col-md-5'),
            Column('debit_desc_3', css_class='form-group col-md-4'),
            Column('debit_amount_3', css_class='form-group col-md-3'),
            css_class=f"accrual-fields {'d-none' if not a3_visible else ''}"
        ))

        # VAT Debit (Dynamic rendering)
        account_rows.append(Row(
            Column('vat_account_id', css_class='form-group col-md-9'),
            Column('vat_usd', css_class='form-group col-md-3'), 
            css_class=f"tax-fields {'d-none' if not vat_visible else ''}"
        ))

        # WHT Expense (Debit) (Dynamic rendering)
        account_rows.append(Row(
            Column('wht_debit_account_id', css_class='form-group col-md-9'),
            Column('wht_amount_dr', css_class='form-group col-md-3'),
            css_class=f"tax-fields {'d-none' if not wht_dr_visible else ''}"
        ))

        # Row 6: Main Credit (Payables Ledger Core)
        account_rows.append(Row(
            Column('credit_account_id', css_class='form-group col-md-9'),
            Column('total_usd', css_class='form-group col-md-3'), 
        ))

        # Row 7: WHT Payable (Credit)
        account_rows.append(Row(
            Column('wht_account_id', css_class='form-group col-md-9'),
            Column('wht_amount_cr', css_class='form-group col-md-3'), 
            css_class=f"tax-fields {'d-none' if not wht_cr_visible else ''}"
        ))

        # --- ASSEMBLE FULL FORM LAYOUT ---
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.disable_csrf = True
        self.helper.layout = Layout(
            Div(
                Row(
                    Column('form_number', css_class='form-group col-md-1'),
                    Column('batch', css_class='form-group col-md-3'),
                    Column('date', css_class='form-group col-md-2'),
                    Column('invoice_no', css_class='form-group col-md-3'),
                    Column('vattin', css_class='form-group col-md-3'),                
                    css_class='mt-4 border-top pt-3 border-2 border-primary' 
                ),
                Row(
                    Column('company', css_class='form-group col-md-4'),
                    Column('vendor_choice', css_class='form-group col-md-3'),
                    Column('payment_status', css_class='form-group col-md-2'),
                    Column('page', css_class='form-group col-md-1'),
                    Column('DELETE', css_class='form-group col-md-2 text-center bg-danger bg-opacity-10 text-danger fw-bold rounded pt-2 pb-2'),
                ),
                
                # Inject dynamic buttons to allow user toggling
                Row(
                    Column(HTML("""
                        <div class="mt-2 mb-2">
                            <button type="button" class="btn btn-sm btn-outline-primary me-2 fw-bold" onclick="this.closest('.purchase-form-wrapper').querySelectorAll('.accrual-fields').forEach(e => e.classList.toggle('d-none'))">
                                + Toggle Accruals
                            </button>
                            <button type="button" class="btn btn-sm btn-outline-info fw-bold" onclick="this.closest('.purchase-form-wrapper').querySelectorAll('.tax-fields').forEach(e => e.classList.toggle('d-none'))">
                                + Toggle Taxes (VAT/WHT)
                            </button>
                            <button type="button" class="btn btn-sm btn-outline-success ms-2 fw-bold" onclick="clonePurchaseForm(this)">
                                + Add Manual Split/Entry
                            </button>
                        </div>
                        <script>
                        if (typeof clonePurchaseForm !== 'function') {
                            function clonePurchaseForm(btn) {
                                const currentForm = btn.closest('.purchase-form-wrapper');
                                
                                // TOGGLE OFF: Hide the cloned form and mark it for deletion in Django
                                if (btn.classList.contains('is-cloned-active')) {
                                    const clonedFormId = btn.getAttribute('data-cloned-target');
                                    const clonedForm = document.getElementById(clonedFormId);
                                    if (clonedForm) {
                                        const deleteCheckbox = clonedForm.querySelector('input[name$="-DELETE"]');
                                        if (deleteCheckbox) {
                                            deleteCheckbox.checked = true;
                                        }
                                        clonedForm.style.display = 'none';
                                    }
                                    btn.classList.remove('is-cloned-active');
                                    btn.classList.replace('btn-outline-danger', 'btn-outline-success');
                                    btn.innerText = '+ Add Manual Split/Entry';
                                    return;
                                }

                                const totalFormsInput = document.querySelector('input[name$="-TOTAL_FORMS"]');
                                if (!totalFormsInput) {
                                    console.error("TOTAL_FORMS input not found.");
                                    return;
                                }
                                
                                let currentTotal = parseInt(totalFormsInput.value);
                                const newForm = currentForm.cloneNode(true);
                                
                                const newFormId = 'cloned-form-' + currentTotal;
                                newForm.id = newFormId;
                                
                                const regex = new RegExp('form-\\\\d+-', 'g');
                                const replaceStr = 'form-' + currentTotal + '-';
                                
                                newForm.querySelectorAll('input, select, textarea').forEach(input => {
                                    if (input.name) input.name = input.name.replace(regex, replaceStr);
                                    if (input.id) input.id = input.id.replace(regex, replaceStr);
                                    
                                    // Clear values for inputs that are not readonly/disabled
                                    if (!input.readOnly && !input.disabled && input.type !== 'hidden') {
                                        if (input.tagName === 'SELECT') { input.selectedIndex = 0; } 
                                        else { input.value = ''; }
                                    }
                                });
                                
                                const formNumberInput = newForm.querySelector('input[name$="-form_number"]');
                                if (formNumberInput) formNumberInput.value = currentTotal + 1;
                                
                                // Remove the clone button from the cloned form to avoid nested cloning confusion
                                const clonedBtn = newForm.querySelector('button[onclick="clonePurchaseForm(this)"]');
                                if (clonedBtn) {
                                    clonedBtn.remove();
                                }
                                
                                currentForm.parentNode.insertBefore(newForm, currentForm.nextSibling);
                                totalFormsInput.value = currentTotal + 1;
                                
                                // TOGGLE ON: Update the button state to allow removal
                                btn.classList.add('is-cloned-active');
                                btn.classList.replace('btn-outline-success', 'btn-outline-danger');
                                btn.innerText = '- Remove Manual Split/Entry';
                                btn.setAttribute('data-cloned-target', newFormId);
                            }
                        }
                        </script>
                    """), css_class='col-md-12'),
                ),
                
                # Injecting structural double-entry array context components
                *account_rows,
                
                Row(   
                    Column('description', css_class='form-group col-md-6'),
                    Column('description_en', css_class='form-group col-md-6'),
                ),
                
                Row(
                    Column('unreg_usd', css_class='form-group col-md-4'),
                    Column('exempt_usd', css_class='form-group col-md-4'),
                    Column('vat_base_usd', css_class='form-group col-md-4'),
                    css_class='bg-light p-2 rounded mt-2 mb-2 border' 
                ),
                Row(
                    Column('instruction', css_class='form-group col-md-12'),
                ),
                Field('vendor', type="hidden"),
                css_class='purchase-form-wrapper'
            )
        )

    def clean(self):
        """Ensure formatting from visual inputs (like commas or $) are stripped before DB save."""
        cleaned_data = super().clean()

        # Clean all money fields first to ensure they are floats for calculation
        money_fields = [
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 
            'total_usd', 'debit_amount_2', 'debit_amount_3', 'net_amount'
        ]
        for f in money_fields:
            val = cleaned_data.get(f)
            if val is not None: # Use `is not None` to correctly handle 0.0
                try:
                    cleaned_data[f] = float(str(val).replace(',', '').replace('$', '').strip())
                except (ValueError, TypeError):
                    cleaned_data[f] = 0.0
            else:
                cleaned_data[f] = 0.0

        net_amount = cleaned_data.get('net_amount', 0.0)
        vat_usd = cleaned_data.get('vat_usd', 0.0)
        debit_amount_2 = cleaned_data.get('debit_amount_2', 0.0)
        debit_amount_3 = cleaned_data.get('debit_amount_3', 0.0)
        total_usd = cleaned_data.get('total_usd', 0.0)
        
        # Smart balancing: Determine which field to trust if user amended one
        if 'net_amount' in self.changed_data and 'total_usd' not in self.changed_data:
            # User explicitly changed the net amount, so update the total credit to match
            cleaned_data['total_usd'] = net_amount + vat_usd + debit_amount_2 + debit_amount_3
        else:
            # Trust the Main Credit (total_usd) as the source of truth
            cleaned_data['net_amount'] = total_usd - vat_usd - debit_amount_2 - debit_amount_3

        return cleaned_data

# ====================================================================
# 3. FORMSET FACTORY
# ====================================================================
PurchaseFormSet = formset_factory(PurchaseReviewForm, extra=0, can_delete=True)

class ManualPurchaseEntryForm(forms.ModelForm):
    vendor_choice = forms.ChoiceField(label="Vendor Selection", required=True)
    
    # DEBITS
    account_id = forms.ChoiceField(
        label="Main Debit Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    vat_account_id = forms.ChoiceField(
        label="VAT Account (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    wht_debit_account_id = forms.ChoiceField(
        label="WHT Expense (Dr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary'})
    )
    
    # CREDITS
    credit_account_id = forms.ChoiceField(
        label="Main Credit Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    wht_account_id = forms.ChoiceField(
        label="WHT Payable (Cr)", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger'})
    )

    def __init__(self, *args, **kwargs):
        vendor_choices = kwargs.pop('vendor_choices', [])
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)

        self.fields['vendor_choice'].choices = vendor_choices

        account_fields = [
            'account_id', 'vat_account_id', 'wht_debit_account_id',
            'credit_account_id', 'wht_account_id',
            'debit_account_id_2', 'debit_account_id_3'
        ]
        for field_name in account_fields:
            if field_name in self.fields:
                self.fields[field_name].choices = account_choices

        # Set default values for manual entry
        self.fields['credit_account_id'].initial = '200000' # Default Trade Payable
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-3'),
                Column('invoice_no', css_class='form-group col-md-3'),
                Column('vattin', css_class='form-group col-md-3'),
            ),
            Row(
                Column('company', css_class='form-group col-md-5'),
                Column('vendor_choice', css_class='form-group col-md-4'),
                Column('payment_status', css_class='form-group col-md-3'),
                css_class='mb-4 border-bottom pb-3'
            ),
            
            # ACCOUNT ROUTING
            Row(Column('account_id', css_class='form-group col-md-12')),
            Row(
                Column('vat_account_id', css_class='form-group col-md-6'),
                Column('wht_debit_account_id', css_class='form-group col-md-6')
            ),
            Row(
                Column('debit_account_id_2', css_class='form-group col-md-4'),
                Column('debit_desc_2', css_class='form-group col-md-4'),
                Column('debit_amount_2', css_class='form-group col-md-4'),
                css_class='bg-light p-2 rounded mt-2'
            ),
            Row(
                Column('debit_account_id_3', css_class='form-group col-md-4'),
                Column('debit_desc_3', css_class='form-group col-md-4'),
                Column('debit_amount_3', css_class='form-group col-md-4'),
                css_class='bg-light p-2 rounded'
            ),
            Row(
                Column('credit_account_id', css_class='form-group col-md-6'),
                Column('wht_account_id', css_class='form-group col-md-6')
            ),
            
            Row(
                Column('description', css_class='form-group col-md-6'),
                Column('description_en', css_class='form-group col-md-6'),
            ),
            
            # FINANCIAL AMOUNTS
            Row(
                Column('unreg_usd', css_class='form-group col-md-2'),
                Column('exempt_usd', css_class='form-group col-md-2'),
                Column('vat_base_usd', css_class='form-group col-md-3'),
                Column('vat_usd', css_class='form-group col-md-2'),
                Column('total_usd', css_class='form-group col-md-3'),
                css_class='bg-light p-3 rounded mt-3 border border-secondary'
            ),
            Field('vendor', type="hidden")
        )

    class Meta:
        model = Purchase
        fields = [
            'date', 'invoice_no', 'company', 'vendor', 'vattin',
            'account_id', 'vat_account_id', 'wht_debit_account_id', 'credit_account_id', 'wht_account_id',
            'debit_account_id_2', 'debit_amount_2', 'debit_desc_2',
            'debit_account_id_3', 'debit_amount_3', 'debit_desc_3',
            'description', 'description_en', 'payment_status',
            'unreg_usd', 'exempt_usd', 'vat_base_usd', 'vat_usd', 'total_usd',
        ]
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'description_en': forms.Textarea(attrs={'rows': 2}),
            'unreg_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'exempt_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_base_usd': forms.TextInput(attrs={'class': 'number-format text-end'}),
            'vat_usd': forms.TextInput(attrs={'class': 'number-format text-end text-primary fw-bold'}),
            'total_usd': forms.TextInput(attrs={'class': 'number-format text-end text-danger fw-bold'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
            'debit_amount_2': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary'}),
            'debit_desc_2': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description for accrual line 2'}),
            'debit_amount_3': forms.TextInput(attrs={'class': 'form-control number-format text-end text-primary'}),
            'debit_desc_3': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description for accrual line 3'}),
        }

class GLMigrationUploadForm(forms.Form):
    gl_file = forms.FileField(
        label="Upload General Ledger Extract (CSV/Excel)",
        help_text="Must contain columns: Date, Vendor / Customer / Employee, Description, No., Debit, Credit"
    )
    batch_name = forms.CharField(
        label="Migration Batch Name", 
        max_length=255, 
        initial="HISTORICAL-MIGRATION-JAN2026",
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    
class GLHistoricalReviewForm(forms.Form):
    gl_no = forms.CharField(label="Voucher/GL No.", required=False)
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    account_id = forms.ChoiceField(label="Account")
    description = forms.CharField(label="Entity / Description")
    debit = forms.FloatField(required=False, label="Debit")
    credit = forms.FloatField(required=False, label="Credit")
    instruction = forms.CharField(
        label="AI Reasoning", required=False, 
        widget=forms.TextInput(attrs={'readonly': 'readonly', 'class': 'text-muted bg-light border-0'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        # Inject 'title' attribute for tooltips on hover
        if self.initial.get('description'):
            self.fields['description'].widget.attrs['title'] = self.initial.get('description')
        if self.initial.get('instruction'):
            self.fields['instruction'].widget.attrs['title'] = self.initial.get('instruction')
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('gl_no', css_class='col-md-2 fw-bold text-primary'),
                Column('date', css_class='col-md-2'),
                Column('account_id', css_class='col-md-3'),
                Column('debit', css_class='col-md-2 text-success'),
                Column('credit', css_class='col-md-2 text-danger'),
                Column('DELETE', css_class='col-md-1 text-center'),
            ),
            Row(
                Column('description', css_class='col-md-6'),
                Column('instruction', css_class='col-md-6'),
            ),
            HTML("<hr>")
        )

# Factory for the new unified form
from django.forms import formset_factory
GLHistoricalFormSet = formset_factory(GLHistoricalReviewForm, extra=0, can_delete=True)

class OldEntryForm(forms.ModelForm):
    account_id = forms.ChoiceField(
        label="GL Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-4'),
                Column('account_id', css_class='form-group col-md-4'),
            ),
            Row(
                Column('description', css_class='form-group col-md-6'),
                Column('instruction', css_class='form-group col-md-6'),
            ),
            Row(
                Column('debit', css_class='form-group col-md-6'),
                Column('credit', css_class='form-group col-md-6'),
            )
        )

    class Meta:
        model = Old
        fields = ['date', 'account_id', 'description', 'instruction', 'debit', 'credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'instruction': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional AI/Manual Reasoning...'}),
        }

class JournalVoucherEntryForm(forms.ModelForm):
    account_id = forms.ChoiceField(
        label="GL Account", required=True, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [])
        super().__init__(*args, **kwargs)
        self.fields['account_id'].choices = account_choices
        
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            HTML("""
                <div class="alert alert-warning mb-3">
                    <strong>Note on Accounting Treatment:</strong> Journal Vouchers currently enforce single-account entries (unbalanced). 
                    For manual double-entry accounting fixes, please use the <strong>Adjustments</strong> module instead.
                </div>
            """),
            Row(
                Column('date', css_class='form-group col-md-4'),
            ),
            Row(
                Column('account_id', css_class='form-group col-md-5'),
                Column('vendor', css_class='form-group col-md-4'),
                Column('payment_status', css_class='form-group col-md-3'),
            ),
            Row(Column('description', css_class='form-group col-md-6'), 
                Column('instruction', css_class='form-group col-md-6'),
            ),
            Row(Column('debit', css_class='form-group col-md-6'), 
            Column('credit', css_class='form-group col-md-6'),
            ),
        )

    class Meta:
        model = JournalVoucher
        fields = ['date', 'account_id', 'vendor', 'payment_status', 'description', 'instruction', 'debit', 'credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
            'instruction': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional Reasoning...'}),
            'payment_status': forms.Select(attrs={'class': 'form-select fw-bold text-warning'}),
        }

class BalancikaExportForm(forms.Form):
    start_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="Start Date"
    )
    end_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="End Date"
    )
    purchase_id = forms.IntegerField(
        required=False,
        label="Purchase ID",
        help_text="Optional: Export specific purchase invoice",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    bank_id = forms.IntegerField(
        required=False,
        label="Bank ID",
        help_text="Optional: Export specific bank charge",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    entry_no_start = forms.IntegerField(
        initial=1,
        label="Starting Entry Number",
        help_text="e.g., 1 will generate PIN00001",
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

class MultiplePDFUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Upload Masterlist Excel File",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx, .xls'})
    )
    # The widget attrs {'multiple': True} allows selecting multiple files in the browser
    pdf_files = forms.FileField(
        widget=forms.FileInput, # Attributes will be set in __init__
        label="Select Proposal PDFs"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Set widget attributes here to avoid potential module-level initialization errors
        self.fields['pdf_files'].widget.attrs.update({
            'multiple': True,
            'class': 'form-control',
            'accept': '.pdf'
        })

class EngagementLetterUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Upload Masterlist Excel File",
        widget=forms.FileInput(attrs={'class': 'form-control border-success', 'accept': '.xlsx, .xls'})
    )
    pdf_files = forms.FileField(
        label="Select Engagement Letter PDFs",
        widget=forms.FileInput
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pdf_files'].widget.attrs.update({
            'multiple': True,
            'class': 'form-control border-success',
            'accept': '.pdf'
        })

class MonthlyClosingForm(forms.Form):
    date = forms.DateField(
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        label="Voucher Date"
    )
    salary_payable = forms.FloatField(
        required=False, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Total Salary Payable (USD)'})
    )
    staff_meals = forms.FloatField(
        required=False, 
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Total Staff Meals (USD)'})
    )
    # Unified File Upload
    tax_declaration_pdf = forms.FileField(
        required=False, 
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.pdf'}), 
        label="Tax Declaration PDF (TOS & Liabilities)"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-3'),
                Column('salary_payable', css_class='form-group col-md-3'),
                Column('staff_meals', css_class='form-group col-md-3'),
            ),
            Row(Column('tax_declaration_pdf', css_class='form-group col-md-12'))
        )

class AccrualForm(forms.Form):
    account_id = forms.ChoiceField(required=False, widget=forms.Select(attrs={'class': 'form-select'}))
    
    # Target class added for JS: 'dynamic-vendor-select'
    vendor = forms.ChoiceField(
        required=False, 
        widget=forms.Select(attrs={'class': 'form-select dynamic-vendor-select', 'autocomplete': 'off'})
    )
    
    description = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description'}))
    debit = forms.FloatField(required=False, widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Amount (USD)'}))
    payment_status = forms.ChoiceField(
        required=False,
        choices=JournalVoucher.PAYMENT_STATUS_CHOICES, 
        initial='Open', 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-warning'})
    )

    def clean(self):
        cleaned_data = super().clean()
        debit = cleaned_data.get('debit')
        
        # Only validate the row if the user actually inputted a monetary amount
        if debit and not cleaned_data.get('DELETE', False):
            if not cleaned_data.get('account_id'):
                self.add_error('account_id', 'Account is required.')
            if not cleaned_data.get('description'):
                self.add_error('description', 'Description is required.')

        # Safe-guard None values to prevent Python TypeError crashes in the view
        if cleaned_data.get('debit') is None:
            cleaned_data['debit'] = 0.0

        return cleaned_data

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [('', '--- Select Account ---')])
        vendor_choices = kwargs.pop('vendor_choices', [('', '--- No Vendor ---')])
        
        super().__init__(*args, **kwargs)
        
        self.fields['account_id'].choices = account_choices
        self.fields['vendor'].choices = vendor_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('account_id', css_class='form-group col-md-2'),
                Column('vendor', css_class='form-group col-md-2'),
                Column('description', css_class='form-group col-md-3'),
                Column('debit', css_class='form-group col-md-2'),
                Column('payment_status', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 text-center mt-4'),
                css_class='align-items-center mb-2 pb-2 border-bottom'
            )
        )

class FXForm(forms.Form):
    account_id = forms.ChoiceField(
        required=False,
        label="FX Gain/Loss Account", 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    bank_account_id = forms.ChoiceField(
        required=False,
        label="KHR Bank Account", 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    description = forms.CharField(
        required=False,
        max_length=255, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Description'})
    )
    openning_balance = forms.FloatField(
        required=False,
        label="Opening Bal (USD)", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    ending_balance = forms.FloatField(
        required=False,
        label="Ending Bal (KHR)", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    exchange_rate = forms.FloatField(
        required=False,
        label="FX Rate", 
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )
    payment_status = forms.ChoiceField(
        required=False,
        choices=JournalVoucher.PAYMENT_STATUS_CHOICES, 
        initial='Paid', 
        widget=forms.Select(attrs={'class': 'form-select fw-bold text-warning'})
    )

    def clean(self):
        cleaned_data = super().clean()
        fx_rate = cleaned_data.get('exchange_rate')

        # Only validate the row if the user actually inputted an exchange rate
        if fx_rate and not cleaned_data.get('DELETE', False):
            if not cleaned_data.get('account_id'):
                self.add_error('account_id', 'FX Account is required.')
            if not cleaned_data.get('bank_account_id'):
                self.add_error('bank_account_id', 'Bank Account is required.')
            if not cleaned_data.get('description'):
                self.add_error('description', 'Description is required.')
            if cleaned_data.get('openning_balance') is None:
                self.add_error('openning_balance', 'Opening Balance is required.')
            if cleaned_data.get('ending_balance') is None:
                self.add_error('ending_balance', 'Ending Balance is required.')

        # Safe-guard None values to prevent Python TypeError crashes in the view
        if cleaned_data.get('exchange_rate') is None: cleaned_data['exchange_rate'] = 0.0
        if cleaned_data.get('openning_balance') is None: cleaned_data['openning_balance'] = 0.0
        if cleaned_data.get('ending_balance') is None: cleaned_data['ending_balance'] = 0.0

        return cleaned_data

    def __init__(self, *args, **kwargs):
        account_choices = kwargs.pop('account_choices', [('', '--- Select Account ---')])
        
        # Remove vendor_choices from kwargs so it doesn't throw a KeyError, 
        # as FX forms no longer use vendors.
        kwargs.pop('vendor_choices', None) 
        
        super().__init__(*args, **kwargs)
        
        # Populate both dropdowns with the Chart of Accounts
        self.fields['account_id'].choices = account_choices
        self.fields['bank_account_id'].choices = account_choices

        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('account_id', css_class='form-group col-md-2'),
                Column('bank_account_id', css_class='form-group col-md-2'),
                Column('description', css_class='form-group col-md-2'),
                Column('openning_balance', css_class='form-group col-md-1'),
                Column('ending_balance', css_class='form-group col-md-1'),
                Column('exchange_rate', css_class='form-group col-md-1'),
                Column('payment_status', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-1 text-center mt-4'),
                css_class='align-items-center mb-2 pb-2 border-bottom'
            )
        )

AccrualFormSet = formset_factory(AccrualForm, extra=3, can_delete=True)
FXFormSet = formset_factory(FXForm, extra=3, can_delete=True)

class AdjustmentEntryForm(forms.ModelForm):
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.none(),
        required=False, label="Vendor",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        required=False, label="Customer",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    debit_account_id = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        label="Debit Account", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-primary fw-bold'})
    )
    credit_account_id = forms.ModelChoiceField(
        queryset=Account.objects.none(),
        label="Credit Account", required=False, 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold'})
    )
    purchase_id = forms.CharField(
        label="Purchase IDs", required=False, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 10, 11'})
    )
    sale_id = forms.CharField(
        label="Sale IDs", required=False, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 20, 21'})
    )
    journal_voucher_id = forms.CharField(
        label="JV IDs", required=False, 
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., 30'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        account_qs = Account.objects.all().order_by('account_id')
        self.fields['debit_account_id'].queryset = account_qs
        self.fields['credit_account_id'].queryset = account_qs
        self.fields['vendor'].queryset = Vendor.objects.all().order_by('vendor_id')
        self.fields['customer'].queryset = Customer.objects.all().order_by('customer_id')
            
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('date', css_class='form-group col-md-2'), 
                Column('vendor', css_class='form-group col-md-2'), 
                Column('customer', css_class='form-group col-md-2'),
                Column('debit_account_id', css_class='form-group col-md-2'), 
                Column('credit_account_id', css_class='form-group col-md-2'),
                Column('DELETE', css_class='form-group col-md-2 text-center mt-4') if 'DELETE' in self.fields else HTML(''),
            ),
            Row(
                Column('purchase_id', css_class='form-group col-md-2'),
                Column('sale_id', css_class='form-group col-md-2'),
                Column('journal_voucher_id', css_class='form-group col-md-2'),
                Column('description', css_class='form-group col-md-4'),
                Column('debit', css_class='form-group col-md-1'), 
                Column('credit', css_class='form-group col-md-1'),
            ),
            HTML('<hr>')
        )

    class Meta:
        model = Adjustment
        fields = ['date', 'vendor', 'customer', 'debit_account_id', 'credit_account_id', 'description', 'debit', 'credit']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 1}),
        }

class BaseAdjustmentFormSet(BaseFormSet):
    def clean(self):
        if any(self.errors):
            return
        total_debit = 0.0
        total_credit = 0.0
        has_forms = False
        for form in self.forms:
            if self.can_delete and self._should_delete_form(form):
                continue
            if not form.cleaned_data:
                continue
            has_forms = True
            debit = form.cleaned_data.get('debit') or 0.0
            credit = form.cleaned_data.get('credit') or 0.0
            total_debit += debit
            total_credit += credit
        
        if has_forms and round(total_debit, 2) != round(total_credit, 2):
            raise ValidationError(f"Total Debit ({total_debit}) must equal Total Credit ({total_credit}) to proceed.")

AdjustmentFormSet = formset_factory(AdjustmentEntryForm, formset=BaseAdjustmentFormSet, extra=4, can_delete=True)

class AdjustmentOffsetForm(forms.Form):
    form_number = forms.CharField(label='No.', disabled=True, required=False)
    # Hidden tracking field
    purchase_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    journal_voucher_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    bank_id = forms.IntegerField(widget=forms.HiddenInput(), required=False)
    
    # Visible fields
    date = forms.DateField(
        required=True,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    bank_date = forms.DateField(
        label='Bank Date',
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control text-muted', 'readonly': True})
    )
    purchase_date = forms.DateField(
        label='Purchase Date',
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control text-muted', 'readonly': True})
    )
    vendor = forms.ModelChoiceField(
        queryset=Vendor.objects.all(), 
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    debit_account_id = forms.ModelChoiceField(
        queryset=Account.objects.all(), 
        widget=forms.Select(attrs={'class': 'form-select text-success fw-bold', 'readonly': True})
    )
    credit_account_id = forms.ModelChoiceField(
        queryset=Account.objects.all(), 
        widget=forms.Select(attrs={'class': 'form-select text-danger fw-bold', 'readonly': True})
    )
    debit = forms.FloatField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': True})
    )
    credit = forms.FloatField(
        widget=forms.NumberInput(attrs={'class': 'form-control', 'readonly': True})
    )
    # UI Reference Field
    partial_offset = forms.BooleanField(
        required=False, 
        widget=forms.CheckboxInput(attrs={'onclick': 'return false;'}) # Prevents user from clicking it
    )
    description = forms.CharField(
        required=False, 
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Numbering the formset rows dynamically based on prefix
        if self.prefix:
            try:
                form_index = int(self.prefix.split('-')[-1]) + 1
                self.fields['form_number'].initial = str(form_index)
            except (ValueError, IndexError):
                self.fields['form_number'].initial = 'N/A'
        else:
            self.fields['form_number'].initial = 'N/A'

        # Inject 'title' attribute for native browser tooltips on hover
        if self.initial.get('description'):
            self.fields['description'].widget.attrs['title'] = self.initial.get('description')
            
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.layout = Layout(
            Row(
                Column('form_number', css_class='form-group col-md-1'),
                Column('date', css_class='form-group col-md-2'),
                Column('vendor', css_class='form-group col-md-7'),
                Column('DELETE', css_class='form-group col-md-2 text-center bg-danger bg-opacity-10 text-danger fw-bold rounded pt-2 pb-2') if 'DELETE' in self.fields else HTML(''),
                css_class='mt-4 border-top pt-3 border-2 border-primary' 
            ),
            Row(
                Column('purchase_date', css_class='form-group col-md-2'),
                Column('debit_account_id', css_class='form-group col-md-4'),
                Column('bank_date', css_class='form-group col-md-2'),                
                Column('credit_account_id', css_class='form-group col-md-4'),
            ),
            Row(
                Column('debit', css_class='form-group col-md-2'),
                Column('credit', css_class='form-group col-md-2'),
                Column('partial_offset', css_class='form-group col-md-2 mt-4'),
            ),
            Row(
                Column('description', css_class='form-group col-md-12'),
            ),
            Field('purchase_id', type="hidden"),
            Field('journal_voucher_id', type="hidden"),
            Field('bank_id', type="hidden"),
            HTML('<hr class="my-4 text-muted border-1">')
        )

# Use standard formset_factory (can_delete=True automatically adds the DELETE checkbox)
OffsetFormSet = formset_factory(AdjustmentOffsetForm, extra=0, can_delete=True)

class ManualInvoiceUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="Upload Compiled Invoices (Excel/CSV)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx, .xls, .csv'})
    )
    batch_name = forms.CharField(
        max_length=100, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., April Hand-Written Invoices'})
    )
    ai_prompt = forms.CharField(
        required=False, label="Batch AI Instructions",
        widget=forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Optional routing rules...'})
    )