from django.db import models

class Purchase(models.Model):
    date = models.DateField()
    company = models.CharField(max_length=100, blank=True, null=True)    
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    vattin = models.CharField(max_length=100, blank=True, null=True)
    vendor_id = models.CharField(max_length=50, blank=True, null=True)
    account_id = models.IntegerField(blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    non_vat_non_tax_payer_usd = models.FloatField(blank=True, null=True)
    non_vat_tax_payer_usd = models.FloatField(blank=True, null=True)
    local_purchase_usd = models.FloatField(blank=True, null=True)
    local_purchase_vat_usd = models.FloatField(blank=True, null=True)
    total_usd = models.FloatField(blank=True, null=True)
    page = models.IntegerField(blank=True, null=True)

    def save(self, *args, **kwargs):
        # Automatically wrap strings in ="VALUE" to protect leading zeros in Excel
        if self.invoice_no and not self.invoice_no.startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
        if self.vattin and not self.vattin.startswith('="'):
            self.vattin = f'="{self.vattin}"'
            
        super(Purchase, self).save(*args, **kwargs)

    def __str__(self):
        return f"Purchase {self.invoice_no} on {self.date}"