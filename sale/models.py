from django.db import models
import re
from tools.models import Client

class Customer(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='customer_client', null=True)
    customer_id = models.CharField(max_length=50) # e.g., V001
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    class Meta:
        # A vendor_id (like V001) should be unique per client, but not globally.
        unique_together = ('client', 'customer_id')

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
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True, related_name='sale_client')
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
