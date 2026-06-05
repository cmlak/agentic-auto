from django.db import models
from django.core.exceptions import ValidationError
from tools.models import Vendor
from django.contrib.auth.models import User
from simple_history.models import HistoricalRecords

# ====================================================================
# --- BANK MODEL ---
# ====================================================================
class Bank(models.Model):
    # DELETED: client = models.ForeignKey(...) - Schema handles isolation now
    
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
    
    # Linked to the isolated Vendor/Customer databases
    vendor = models.ForeignKey('tools.Vendor', on_delete=models.CASCADE, null=True, blank=True)
    customer = models.ForeignKey('sale.Customer', on_delete=models.CASCADE, null=True, blank=True)
    
    # Financials
    debit = models.FloatField(default=0.0)
    credit = models.FloatField(default=0.0)
    balance = models.FloatField(default=0.0)
    
    # --- AI & RECONCILIATION FIELDS ---
    matched_purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='bank_payments')
    matched_sale = models.ForeignKey('sale.Sale', on_delete=models.CASCADE, null=True, blank=True, related_name='bank_receipts')
    matched_jv = models.ForeignKey('tools.JournalVoucher', on_delete=models.CASCADE, null=True, blank=True, related_name='bank_payments')
    matched_purchase_ids = models.CharField(max_length=255, blank=True, null=True)
    matched_sale_ids = models.CharField(max_length=255, blank=True, null=True)
    matched_jv_ids = models.CharField(max_length=255, blank=True, null=True)
    instruction = models.TextField(blank=True, null=True) # Stores AI Reasoning
    debit_account_id = models.CharField(max_length=20, blank=True, null=True)
    credit_account_id = models.CharField(max_length=20, blank=True, null=True)
    fee_account_id = models.CharField(max_length=20, blank=True, null=True)
    fee_amount = models.FloatField(default=0.0)
    
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    # ADDED: The django-simple-history audit trail
    history = HistoricalRecords()

    def __str__(self):
        return f"{self.date} | {self.bank_ref_id} | In: {self.debit} | Out: {self.credit}"
        
    def save(self, *args, **kwargs):
        old_p_ids = set()
        old_s_ids = set()
        old_jv_ids = set()
        if self.pk:
            old_instance = Bank.objects.filter(pk=self.pk).first()
            if old_instance:
                if old_instance.matched_purchase_ids:
                    old_p_ids = set([int(x) for x in str(old_instance.matched_purchase_ids).split(',') if x.strip().isdigit()])
                elif old_instance.matched_purchase_id:
                    old_p_ids = {old_instance.matched_purchase_id}

                if getattr(old_instance, 'matched_sale_ids', None):
                    old_s_ids = set([int(x) for x in str(old_instance.matched_sale_ids).split(',') if x.strip().isdigit()])
                elif getattr(old_instance, 'matched_sale_id', None):
                    old_s_ids = {old_instance.matched_sale_id}

                if getattr(old_instance, 'matched_jv_ids', None):
                    old_jv_ids = set([int(x) for x in str(old_instance.matched_jv_ids).split(',') if x.strip().isdigit()])
                elif getattr(old_instance, 'matched_jv_id', None):
                    old_jv_ids = {old_instance.matched_jv_id}
        
        super().save(*args, **kwargs)
        
        # Lazy imports to prevent circular dependencies
        from tools.models import Purchase, JournalVoucher
        try:
            from sale.models import Sale
        except ImportError:
            Sale = None

        new_p_ids = set()
        if self.matched_purchase_ids:
            new_p_ids = set([int(x) for x in str(self.matched_purchase_ids).split(',') if x.strip().isdigit()])
        elif self.matched_purchase_id:
            new_p_ids = {self.matched_purchase_id}
            
        new_s_ids = set()
        if getattr(self, 'matched_sale_ids', None) and Sale:
            new_s_ids = set([int(x) for x in str(self.matched_sale_ids).split(',') if x.strip().isdigit()])
        elif getattr(self, 'matched_sale_id', None):
            new_s_ids = {self.matched_sale_id}

        new_jv_ids = set()
        if getattr(self, 'matched_jv_ids', None):
            new_jv_ids = set([int(x) for x in str(self.matched_jv_ids).split(',') if x.strip().isdigit()])
        elif getattr(self, 'matched_jv_id', None):
            new_jv_ids = {self.matched_jv_id}

        # Update removed linkages to 'Open'
        removed_p_ids = old_p_ids - new_p_ids
        if removed_p_ids:
            purchases = Purchase.objects.filter(id__in=removed_p_ids)
            for p in purchases:
                if not p.bank_payments.exists() and not getattr(p, 'cash_payments', Purchase.objects.none()).exists():
                    p.payment_status = 'Open'
                    p.save(update_fields=['payment_status'])

        removed_s_ids = old_s_ids - new_s_ids
        if removed_s_ids and Sale:
            sales = Sale.objects.filter(id__in=removed_s_ids)
            for s in sales:
                if not getattr(s, 'bank_receipts', None).exists() and not getattr(s, 'cash_receipts', None).exists():
                    s.payment_status = 'Open'
                    s.save(update_fields=['payment_status'])

        removed_jv_ids = old_jv_ids - new_jv_ids
        if removed_jv_ids:
            jvs = JournalVoucher.objects.filter(id__in=removed_jv_ids)
            for jv in jvs:
                if not getattr(jv, 'bank_payments', None).exists() and not getattr(jv, 'cash_payments', None).exists():
                    jv.payment_status = 'Open'
                    jv.save(update_fields=['payment_status'])

        # Update new linkages to 'Paid'
        if new_p_ids:
            Purchase.objects.filter(id__in=new_p_ids).exclude(payment_status='Paid').update(payment_status='Paid')
            
        if new_s_ids and Sale:
            Sale.objects.filter(id__in=new_s_ids).exclude(payment_status='Paid').update(payment_status='Paid')
            
        if new_jv_ids:
            JournalVoucher.objects.filter(id__in=new_jv_ids).exclude(payment_status='Paid').update(payment_status='Paid')

    def delete(self, *args, **kwargs):
        from tools.models import Purchase, JournalVoucher
        try:
            from sale.models import Sale
        except ImportError:
            Sale = None

        p_ids = []
        if getattr(self, 'matched_purchase_ids', None):
            p_ids = [int(x) for x in str(self.matched_purchase_ids).split(',') if x.strip().isdigit()]
        elif self.matched_purchase_id:
            p_ids = [self.matched_purchase_id]

        s_ids = []
        if getattr(self, 'matched_sale_ids', None):
            s_ids = [int(x) for x in str(self.matched_sale_ids).split(',') if x.strip().isdigit()]
        elif getattr(self, 'matched_sale_id', None):
            s_ids = [self.matched_sale_id]

        jv_ids = []
        if getattr(self, 'matched_jv_ids', None):
            jv_ids = [int(x) for x in str(self.matched_jv_ids).split(',') if x.strip().isdigit()]
        elif getattr(self, 'matched_jv_id', None):
            jv_ids = [self.matched_jv_id]

        result = super().delete(*args, **kwargs)
        
        # Revert payment status when deleting the Bank record
        if p_ids:
            purchases = Purchase.objects.filter(id__in=p_ids)
            for purchase in purchases:
                if not purchase.bank_payments.exists() and not getattr(purchase, 'cash_payments', Purchase.objects.none()).exists():
                    purchase.payment_status = 'Open'
                    purchase.save(update_fields=['payment_status'])
                    
        if s_ids and Sale:
            sales = Sale.objects.filter(id__in=s_ids)
            for sale in sales:
                if not getattr(sale, 'bank_receipts', None).exists() and not getattr(sale, 'cash_receipts', None).exists():
                    sale.payment_status = 'Open'
                    sale.save(update_fields=['payment_status'])
                    
        if jv_ids:
            jvs = JournalVoucher.objects.filter(id__in=jv_ids)
            for jv in jvs:
                if not getattr(jv, 'bank_payments', None).exists() and not getattr(jv, 'cash_payments', None).exists():
                    jv.payment_status = 'Open'
                    jv.save(update_fields=['payment_status'])
                    
        return result


# ====================================================================
# --- CASH MODEL ---
# ====================================================================
class Cash(models.Model):
    # DELETED: client = models.ForeignKey(...) - Schema handles isolation now
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.CharField(max_length=255, blank=True, null=True)
    
    # Core Transaction Data
    date = models.DateField(blank=True, null=True)
    voucher_no = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    
    # Linked to the isolated Vendor/Customer databases
    vendor = models.ForeignKey('tools.Vendor', on_delete=models.CASCADE, null=True, blank=True)
    customer = models.ForeignKey('sale.Customer', on_delete=models.CASCADE, null=True, blank=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    # Financials
    debit = models.FloatField(default=0.0)   # Money In
    credit = models.FloatField(default=0.0)  # Money Out
    balance = models.FloatField(default=0.0)
    
    # --- AI & RECONCILIATION FIELDS ---
    matched_purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='cash_payments')
    matched_sale = models.ForeignKey('sale.Sale', on_delete=models.CASCADE, null=True, blank=True, related_name='cash_receipts')
    matched_jv = models.ForeignKey('tools.JournalVoucher', on_delete=models.CASCADE, null=True, blank=True, related_name='cash_payments')
    matched_purchase_ids = models.CharField(max_length=255, blank=True, null=True)
    matched_sale_ids = models.CharField(max_length=255, blank=True, null=True)
    matched_jv_ids = models.CharField(max_length=255, blank=True, null=True)
    instruction = models.TextField(blank=True, null=True) # Stores AI Reasoning
    debit_account_id = models.CharField(max_length=20, blank=True, null=True)
    credit_account_id = models.CharField(max_length=20, blank=True, null=True)
    fee_account_id = models.CharField(max_length=20, blank=True, null=True)
    fee_amount = models.FloatField(default=0.0)
    
    # Additional Context
    note = models.TextField(blank=True, null=True)

    # ADDED: The django-simple-history audit trail
    history = HistoricalRecords()

    def __str__(self):
        desc = self.description[:30] + '...' if self.description and len(self.description) > 30 else self.description
        return f"{self.date} | {desc} | In: {self.debit} | Out: {self.credit}"

    def save(self, *args, **kwargs):
        old_p_ids = set()
        old_s_ids = set()
        old_jv_ids = set()
        if self.pk:
            old_instance = Cash.objects.filter(pk=self.pk).first()
            if old_instance:
                if old_instance.matched_purchase_ids:
                    old_p_ids = set([int(x) for x in str(old_instance.matched_purchase_ids).split(',') if x.strip().isdigit()])
                elif old_instance.matched_purchase_id:
                    old_p_ids = {old_instance.matched_purchase_id}

                if getattr(old_instance, 'matched_sale_ids', None):
                    old_s_ids = set([int(x) for x in str(old_instance.matched_sale_ids).split(',') if x.strip().isdigit()])
                elif getattr(old_instance, 'matched_sale_id', None):
                    old_s_ids = {old_instance.matched_sale_id}

                if getattr(old_instance, 'matched_jv_ids', None):
                    old_jv_ids = set([int(x) for x in str(old_instance.matched_jv_ids).split(',') if x.strip().isdigit()])
                elif getattr(old_instance, 'matched_jv_id', None):
                    old_jv_ids = {old_instance.matched_jv_id}
        
        super().save(*args, **kwargs)
        
        from tools.models import Purchase, JournalVoucher
        try:
            from sale.models import Sale
        except ImportError:
            Sale = None

        new_p_ids = set()
        if self.matched_purchase_ids:
            new_p_ids = set([int(x) for x in str(self.matched_purchase_ids).split(',') if x.strip().isdigit()])
        elif self.matched_purchase_id:
            new_p_ids = {self.matched_purchase_id}
            
        new_s_ids = set()
        if getattr(self, 'matched_sale_ids', None) and Sale:
            new_s_ids = set([int(x) for x in str(self.matched_sale_ids).split(',') if x.strip().isdigit()])
        elif getattr(self, 'matched_sale_id', None):
            new_s_ids = {self.matched_sale_id}

        new_jv_ids = set()
        if getattr(self, 'matched_jv_ids', None):
            new_jv_ids = set([int(x) for x in str(self.matched_jv_ids).split(',') if x.strip().isdigit()])
        elif getattr(self, 'matched_jv_id', None):
            new_jv_ids = {self.matched_jv_id}

        # Update removed linkages to 'Open'
        removed_p_ids = old_p_ids - new_p_ids
        if removed_p_ids:
            purchases = Purchase.objects.filter(id__in=removed_p_ids)
            for p in purchases:
                if not getattr(p, 'bank_payments', None).exists() and not p.cash_payments.exists():
                    p.payment_status = 'Open'
                    p.save(update_fields=['payment_status'])

        removed_s_ids = old_s_ids - new_s_ids
        if removed_s_ids and Sale:
            sales = Sale.objects.filter(id__in=removed_s_ids)
            for s in sales:
                if not getattr(s, 'bank_receipts', None).exists() and not getattr(s, 'cash_receipts', None).exists():
                    s.payment_status = 'Open'
                    s.save(update_fields=['payment_status'])

        removed_jv_ids = old_jv_ids - new_jv_ids
        if removed_jv_ids:
            jvs = JournalVoucher.objects.filter(id__in=removed_jv_ids)
            for jv in jvs:
                if not getattr(jv, 'bank_payments', None).exists() and not getattr(jv, 'cash_payments', None).exists():
                    jv.payment_status = 'Open'
                    jv.save(update_fields=['payment_status'])

        # Update new linkages to 'Paid'
        if new_p_ids:
            Purchase.objects.filter(id__in=new_p_ids).exclude(payment_status='Paid').update(payment_status='Paid')
            
        if new_s_ids and Sale:
            Sale.objects.filter(id__in=new_s_ids).exclude(payment_status='Paid').update(payment_status='Paid')
            
        if new_jv_ids:
            JournalVoucher.objects.filter(id__in=new_jv_ids).exclude(payment_status='Paid').update(payment_status='Paid')

    def delete(self, *args, **kwargs):
        from tools.models import Purchase, JournalVoucher
        try:
            from sale.models import Sale
        except ImportError:
            Sale = None

        p_ids = []
        if getattr(self, 'matched_purchase_ids', None):
            p_ids = [int(x) for x in str(self.matched_purchase_ids).split(',') if x.strip().isdigit()]
        elif self.matched_purchase_id:
            p_ids = [self.matched_purchase_id]

        s_ids = []
        if getattr(self, 'matched_sale_ids', None):
            s_ids = [int(x) for x in str(self.matched_sale_ids).split(',') if x.strip().isdigit()]
        elif getattr(self, 'matched_sale_id', None):
            s_ids = [self.matched_sale_id]

        jv_ids = []
        if getattr(self, 'matched_jv_ids', None):
            jv_ids = [int(x) for x in str(self.matched_jv_ids).split(',') if x.strip().isdigit()]
        elif getattr(self, 'matched_jv_id', None):
            jv_ids = [self.matched_jv_id]

        result = super().delete(*args, **kwargs)
        
        # Revert payment status when deleting the Cash record
        if p_ids:
            purchases = Purchase.objects.filter(id__in=p_ids)
            for purchase in purchases:
                if not getattr(purchase, 'bank_payments', None).exists() and not purchase.cash_payments.exists():
                    purchase.payment_status = 'Open'
                    purchase.save(update_fields=['payment_status'])
                    
        if s_ids and Sale:
            sales = Sale.objects.filter(id__in=s_ids)
            for sale in sales:
                if not getattr(sale, 'bank_receipts', None).exists() and not getattr(sale, 'cash_receipts', None).exists():
                    sale.payment_status = 'Open'
                    sale.save(update_fields=['payment_status'])
                    
        if jv_ids:
            jvs = JournalVoucher.objects.filter(id__in=jv_ids)
            for jv in jvs:
                if not getattr(jv, 'bank_payments', None).exists() and not getattr(jv, 'cash_payments', None).exists():
                    jv.payment_status = 'Open'
                    jv.save(update_fields=['payment_status'])
                    
        return result