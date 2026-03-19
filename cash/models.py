from django.db import models
from django.core.exceptions import ValidationError
from tools.models import Client, Vendor
from django.contrib.auth.models import User

class Bank(models.Model):
    # Relational & Meta Data
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.CharField(max_length=255, blank=True, null=True)
    sys_id = models.CharField(max_length=50, blank=True, null=True)
    
    # Core Transaction Data
    date = models.DateField(blank=True, null=True) 
    bank_ref_id = models.CharField(max_length=100, blank=True, null=True)
    trans_type = models.CharField(max_length=100, blank=True, null=True)
    counterparty = models.CharField(max_length=255, blank=True, null=True)
    purpose = models.TextField(blank=True, null=True)
    remark = models.CharField(max_length=255, blank=True, null=True)
    raw_remark = models.TextField(blank=True, null=True)
    
    # Financials
    debit = models.FloatField(default=0.0)
    credit = models.FloatField(default=0.0)
    balance = models.FloatField(default=0.0)
    
    # --- AI & RECONCILIATION FIELDS ---
    matched_purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='bank_payments')
    instruction = models.TextField(blank=True, null=True) # Stores AI Reasoning
    debit_account_id = models.CharField(max_length=20, blank=True, null=True)
    credit_account_id = models.CharField(max_length=20, blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        # Protect reference IDs from converting to scientific notation in Excel
        if self.bank_ref_id and not str(self.bank_ref_id).startswith('="'):
            self.bank_ref_id = f'="{self.bank_ref_id}"'
        super(Bank, self).save(*args, **kwargs)

    def __str__(self):
        return f"{self.date} | {self.bank_ref_id} | In: {self.debit} | Out: {self.credit}"


class Cash(models.Model):
    # Relational & Meta Data (Multi-Tenant Isolation)
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.CharField(max_length=255, blank=True, null=True)
    
    # Core Transaction Data
    date = models.DateField(blank=True, null=True)
    voucher_no = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Linked to the isolated Vendor database
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, null=True, blank=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    # Financials
    debit = models.FloatField(default=0.0)   # Money In
    credit = models.FloatField(default=0.0)  # Money Out
    balance = models.FloatField(default=0.0)
    
    # --- AI & RECONCILIATION FIELDS ---
    matched_purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='cash_payments')
    instruction = models.TextField(blank=True, null=True) # Stores AI Reasoning
    debit_account_id = models.CharField(max_length=20, blank=True, null=True)
    credit_account_id = models.CharField(max_length=20, blank=True, null=True)
    
    # Additional Context
    note = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        # Protect invoice numbers from converting to scientific notation in Excel exports
        if self.invoice_no and not str(self.invoice_no).startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
        super(Cash, self).save(*args, **kwargs)

    def __str__(self):
        desc = self.description[:30] + '...' if self.description and len(self.description) > 30 else self.description
        return f"{self.date} | {desc} | In: {self.debit} | Out: {self.credit}"