from django.db import models
import re
from simple_history.models import HistoricalRecords


class Customer(models.Model):
    customer_id = models.CharField(max_length=50) # e.g., V001
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    # ADDED: The django-simple-history audit trail
    history = HistoricalRecords()

    def save(self, *args, **kwargs):
        if self.name:
            name_str = str(self.name).lower().replace('&', ' and ')
            self.normalized_name = re.sub(r'[\W_]+', ' ', name_str).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer_id} - {self.name}"

# ====================================================================
# --- 3. SALE MODEL ---
# ====================================================================
class Sale(models.Model):
    batch = models.CharField(max_length=255, blank=True, null=True) 
    
    date = models.DateField(blank=True, null=True)
    company = models.CharField(max_length=100, blank=True, null=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    debit_account_id = models.IntegerField(blank=True, null=True)
    vat_output_id = models.IntegerField(blank=True, null=True)
    credit_account_id = models.IntegerField(blank=True, null=True, default=200000)

    description = models.TextField(blank=True, null=True)
    instruction = models.TextField(blank=True, null=True) 

    # ADDED: The django-simple-history audit trail
    history = HistoricalRecords()
    
    PAYMENT_STATUS_CHOICES = [
        ('Open', 'Open'),
        ('Prepayment', 'Prepayment'),
        ('Paid', 'Paid'),
    ]
    payment_status = models.CharField(max_length=50, choices=PAYMENT_STATUS_CHOICES, default='Open')

    vat_base_usd = models.FloatField(blank=True, null=True)
    vat_usd = models.FloatField(blank=True, null=True)
    total_usd = models.FloatField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        # Double-check cleanup to prevent ="null" or ="1"
        if self.invoice_no and str(self.invoice_no).lower() in ['null', 'none', 'unknown', '1']:
            self.invoice_no = None
        if self.invoice_no and not str(self.invoice_no).startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
            
        super(Sale, self).save(*args, **kwargs)

    def __str__(self):
        return f"{self.customer_id} - {self.invoice_no}"
