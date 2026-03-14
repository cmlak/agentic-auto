from django.db import models
import re

class Client(models.Model):
    name = models.CharField(max_length=255, unique=True)
    code = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., CCKT")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} - {self.name}" if self.code else self.name


# ====================================================================
# --- 2. VENDOR MODEL (Isolated per Client) ---
# ====================================================================
class Vendor(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='vendors', null=True)
    vendor_id = models.CharField(max_length=50) # e.g., V001
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    class Meta:
        # A vendor_id (like V001) should be unique per client, but not globally.
        unique_together = ('client', 'vendor_id')

    def save(self, *args, **kwargs):
        if self.name:
            name_str = str(self.name).lower().replace('&', ' and ')
            self.normalized_name = re.sub(r'[\W_]+', ' ', name_str).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.vendor_id} - {self.name}"

# ====================================================================
# --- 3. PURCHASE MODEL ---
# ====================================================================
class Purchase(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE, null=True)
    batch = models.CharField(max_length=255, blank=True, null=True) 
    
    date = models.DateField(blank=True, null=True)
    company = models.CharField(max_length=100, blank=True, null=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True)
    
    vattin = models.CharField(max_length=100, blank=True, null=True)
    account_id = models.IntegerField(blank=True, null=True)
    wht_debit_account_id = models.IntegerField(blank=True, null=True)
    vat_account_id = models.IntegerField(blank=True, null=True)
    credit_account_id = models.IntegerField(blank=True, null=True, default=200000)
    wht_account_id = models.IntegerField(blank=True, null=True)

    description = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)
    instruction = models.TextField(blank=True, null=True) 
    
    PAYMENT_STATUS_CHOICES = [
        ('Open', 'Open'),
        ('Prepayment', 'Prepayment'),
        ('Paid', 'Paid'),
    ]
    payment_status = models.CharField(max_length=50, choices=PAYMENT_STATUS_CHOICES, default='Open')

    unreg_usd = models.FloatField(blank=True, null=True)
    exempt_usd = models.FloatField(blank=True, null=True)
    vat_base_usd = models.FloatField(blank=True, null=True)
    vat_usd = models.FloatField(blank=True, null=True)
    total_usd = models.FloatField(blank=True, null=True)
    page = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        # Double-check cleanup to prevent ="null" or ="1"
        if self.invoice_no and str(self.invoice_no).lower() in ['null', 'none', 'unknown', '1']:
            self.invoice_no = None
        if self.invoice_no and not str(self.invoice_no).startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
            
        if self.vattin and str(self.vattin).lower() in ['null', 'none', 'unknown']:
            self.vattin = None
        if self.vattin and not str(self.vattin).startswith('="'):
            self.vattin = f'="{self.vattin}"'
        super(Purchase, self).save(*args, **kwargs)

class AICostLog(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255)
    total_pages = models.IntegerField()
    flash_cost = models.FloatField(default=0.0)
    pro_cost = models.FloatField(default=0.0)
    total_cost = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def __str__(self):
        return f"{self.date.strftime('%Y-%m-%d %H:%M')} - {self.file_name} (${self.total_cost})"