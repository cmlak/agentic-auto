from django.db import models
from django.contrib.auth.models import User
from datetime import date
from dateutil.relativedelta import relativedelta # pip install python-dateutil

class Capitalization(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    batch = models.CharField(max_length=255, blank=True, null=True) 
    
    date = models.DateField(blank=True, null=True)
    company = models.CharField(max_length=100, blank=True, null=True)
    invoice_no = models.CharField(max_length=100, blank=True, null=True)
    
    vendor = models.ForeignKey('tools.Vendor', on_delete=models.SET_NULL, null=True, blank=True)
    
    vattin = models.CharField(max_length=100, blank=True, null=True)
    debit_account_id = models.IntegerField(blank=True, null=True, default=181000) # Factory Construction in Progress 建设厂房-待摊销资产
    wht_debit_account_id = models.IntegerField(blank=True, null=True, default=725420)
    vat_debit_account_id = models.IntegerField(blank=True, null=True, default=115010)
    credit_account_id = models.IntegerField(blank=True, null=True, default=200000) # Trade Payable - USD 应付账款 - 美元

    description = models.TextField(blank=True, null=True, help_text="e.g. commercial invoice, custom declaration, THC/DO, port charge, clearance and trucking")
    description_en = models.TextField(blank=True, null=True)
    instruction = models.TextField(blank=True, null=True) 
    
    capitalization = models.TextField(blank=True, null=True, help_text="Basis of capitalization value (e.g. vendor price, COP, prorated THC/DO...)")

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
    wht_usd = models.FloatField(blank=True, null=True)
    total_usd = models.FloatField(blank=True, null=True)
    page = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def save(self, *args, **kwargs):
        if self.invoice_no and str(self.invoice_no).lower() in ['null', 'none', 'unknown', '1']:
            self.invoice_no = None
        if self.invoice_no and not str(self.invoice_no).startswith('="'):
            self.invoice_no = f'="{self.invoice_no}"'
            
        if self.vattin and str(self.vattin).lower() in ['null', 'none', 'unknown']:
            self.vattin = None
        if self.vattin and not str(self.vattin).startswith('="'):
            self.vattin = f'="{self.vattin}"'
        super(Capitalization, self).save(*args, **kwargs)


class AssetBatch(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    batch_id = models.CharField(max_length=255, unique=True) # e.g. INV2023-001-1
    source_file = models.CharField(max_length=255, blank=True, null=True)
    invoice_number = models.CharField(max_length=100, blank=True, null=True)
    date = models.DateField(blank=True, null=True)
    total_invoice_value = models.FloatField(blank=True, null=True, default=0.0)
    total_invoice_weight = models.FloatField(blank=True, null=True, default=0.0)
    
    # Line Item details
    item_name = models.CharField(max_length=255, blank=True, null=True)
    cdc = models.CharField(max_length=100, blank=True, null=True)
    quantity = models.FloatField(blank=True, null=True, default=0.0)
    unit = models.CharField(max_length=50, blank=True, null=True)
    unit_price = models.FloatField(blank=True, null=True, default=0.0)
    amount_usd = models.FloatField(blank=True, null=True, default=0.0)
    item_gross_weight_kg = models.FloatField(blank=True, null=True, default=0.0)
    
    # Customs
    customs_declaration_number = models.CharField(max_length=100, blank=True, null=True)
    custom_duty_usd = models.FloatField(blank=True, null=True, default=0.0)
    special_tax_usd = models.FloatField(blank=True, null=True, default=0.0)
    value_added_tax_usd = models.FloatField(blank=True, null=True, default=0.0)
    
    # Auxiliary Aggregates
    auxiliary_invoice_numbers = models.TextField(blank=True, null=True)
    total_freight_usd = models.FloatField(blank=True, null=True, default=0.0)
    total_insurance_usd = models.FloatField(blank=True, null=True, default=0.0)
    total_thc_usd = models.FloatField(blank=True, null=True, default=0.0)
    total_port_charges_usd = models.FloatField(blank=True, null=True, default=0.0)
    total_clearance_trucking_usd = models.FloatField(blank=True, null=True, default=0.0)
    net_reimbursement_usd = models.FloatField(blank=True, null=True, default=0.0)
    
    # Prorated Values
    prorated_insurance_usd = models.FloatField(blank=True, null=True, default=0.0)
    prorated_net_reimb_usd = models.FloatField(blank=True, null=True, default=0.0)
    prorated_freight_usd = models.FloatField(blank=True, null=True, default=0.0)
    prorated_thc_usd = models.FloatField(blank=True, null=True, default=0.0)
    prorated_port_charges_usd = models.FloatField(blank=True, null=True, default=0.0)
    prorated_clearance_trucking_usd = models.FloatField(blank=True, null=True, default=0.0)
    
    # Final Value
    capitalized_value_usd = models.FloatField(blank=True, null=True, default=0.0)

    created_at = models.DateTimeField(auto_now_add=True, blank=True, null=True)

    def __str__(self):
        return f"{self.batch_id} - {self.item_name}"

class Asset(models.Model):
    ASSET_TYPES = [
        ('FURNITURE', 'Furniture & fittings 家具与设备'),
        ('VEHICLE', 'Motor and Vehicle 机动车辆'),
        ('EQUIPMENT', 'Machinery & Equipment 机械设备'),
        ('OFFICE EQUIPMENT', 'Office Equipment 办公室设备'),
        ('COMPUTER & IT', 'Computer & IT Equipment 电脑与IT设备'),
        ('Building', 'Building 建筑物'),
        ('Renovation', 'Renovation 装修'),
    ]
    DEPRECIATION_METHODS = [
        ('SL', 'Straight Line 直线法'),
        ('DB', 'Declining Balance 余额递减法'),
    ]
    STATUS = [
        ('ACTIVE', 'Active 在用'),
        ('DISPOSED', 'Disposed 已处置'),
    ]

    asset_code = models.CharField(max_length=50, unique=True)
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPES)
    status = models.CharField(max_length=20, choices=STATUS, default='ACTIVE')
    
    # Links to AP
    purchase = models.OneToOneField('tools.Purchase', on_delete=models.SET_NULL, null=True)
    capitalization_record = models.ForeignKey('Capitalization', on_delete=models.SET_NULL, null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, help_text="Capitalized Cost")
    
    # GL Account Mappings
    asset_account = models.ForeignKey('account.Account', related_name='asset_acct', on_delete=models.PROTECT)
    acc_dep_account = models.ForeignKey('account.Account', related_name='acc_dep_acct', on_delete=models.PROTECT)
    dep_expense_account = models.ForeignKey('account.Account', related_name='dep_exp_acct', on_delete=models.PROTECT)
    
    # Depreciation Settings
    depreciation_start_date = models.DateField(blank=True, null=True)
    depreciation_method = models.CharField(max_length=5, choices=DEPRECIATION_METHODS, default='SL')
    useful_life_months = models.IntegerField(help_text="e.g. 5.5 years = 66 months")
    salvage_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    description = models.TextField(blank=True, null=True)

    @property
    def accumulated_depreciation(self):
        # Dynamically calculates total from the ledger
        return sum(entry.amount for entry in self.depreciation_entries.all())

    @property
    def net_book_value(self):
        return self.purchase_cost - self.accumulated_depreciation

class DepreciationEntry(models.Model):
    """Audit trail for monthly depreciation runs"""
    asset = models.ForeignKey(Asset, related_name='depreciation_entries', on_delete=models.CASCADE)
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    journal_entry = models.OneToOneField('account.JournalEntry', on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

class AssetDisposal(models.Model):
    """Records the retirement/sale of an asset"""
    asset = models.OneToOneField(Asset, related_name='disposal_record', on_delete=models.CASCADE)
    disposal_date = models.DateField()
    proceeds = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    disposal_income_account = models.ForeignKey('account.Account', related_name='disposal_inc_acct', on_delete=models.PROTECT)
    net_book_value_at_disposal = models.DecimalField(max_digits=12, decimal_places=2)
    gain_loss_amount = models.DecimalField(max_digits=12, decimal_places=2)
    journal_entry = models.OneToOneField('account.JournalEntry', on_delete=models.SET_NULL, null=True)