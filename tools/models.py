from django.db import models
import re

# NEW: Dedicated Vendor Model
class Vendor(models.Model):
    vendor_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, db_index=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-normalize the name before saving to the database
        if self.name:
            name_str = str(self.name).lower().replace('&', ' and ')
            self.normalized_name = re.sub(r'[\W_]+', ' ', name_str).strip()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.vendor_id} - {self.name}"

class Purchase(models.Model):
    date = models.DateField()
    company = models.CharField(max_length=100, blank=True, null=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    # Updated: Store both ID and Name for easier review and reporting
    vendor = models.ForeignKey(Vendor, on_delete=models.SET_NULL, null=True, blank=True)
    
    vattin = models.CharField(max_length=100, blank=True, null=True)
    account_id = models.IntegerField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    description_en = models.TextField(blank=True, null=True)
    non_vat_non_tax_payer_usd = models.FloatField(blank=True, null=True)
    non_vat_tax_payer_usd = models.FloatField(blank=True, null=True)
    local_purchase_usd = models.FloatField(blank=True, null=True)
    local_purchase_vat_usd = models.FloatField(blank=True, null=True)
    total_usd = models.FloatField(blank=True, null=True)
    page = models.IntegerField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.invoice_no and not self.invoice_no.startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
        if self.vattin and not self.vattin.startswith('="'):
            self.vattin = f'="{self.vattin}"'
        super(Purchase, self).save(*args, **kwargs)

class AICostLog(models.Model):
    date = models.DateTimeField(auto_now_add=True)
    file_name = models.CharField(max_length=255)
    total_pages = models.IntegerField()
    flash_cost = models.FloatField(default=0.0)
    pro_cost = models.FloatField(default=0.0)
    total_cost = models.FloatField(default=0.0)

    def __str__(self):
        return f"{self.date.strftime('%Y-%m-%d %H:%M')} - {self.file_name} (${self.total_cost})"