from django.db import models
from django.contrib.auth.models import User
from datetime import date
from dateutil.relativedelta import relativedelta # pip install python-dateutil

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
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, help_text="Capitalized Cost")
    
    # GL Account Mappings
    asset_account = models.ForeignKey('account.Account', related_name='asset_acct', on_delete=models.PROTECT)
    acc_dep_account = models.ForeignKey('account.Account', related_name='acc_dep_acct', on_delete=models.PROTECT)
    dep_expense_account = models.ForeignKey('account.Account', related_name='dep_exp_acct', on_delete=models.PROTECT)
    
    # Depreciation Settings
    depreciation_start_date = models.DateField()
    depreciation_method = models.CharField(max_length=5, choices=DEPRECIATION_METHODS, default='SL')
    useful_life_months = models.IntegerField(help_text="e.g. 5.5 years = 66 months")
    salvage_value = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

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