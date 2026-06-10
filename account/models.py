from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from simple_history.models import HistoricalRecords
from django.utils.translation import gettext_lazy as _

# ====================================================================
# --- 1. CHART OF ACCOUNTS ---
# ====================================================================
class Account(models.Model):
    ACCOUNT_TYPES = [
        ('Asset', 'Asset'),
        ('Liability', 'Liability'),
        ('Equity', 'Equity'),
        ('Revenue', 'Revenue'),
        ('Expense', 'Expense'),
    ]

    # DELETED: client = models.ForeignKey(...)
    
    # ADDED: unique=True because this table is now isolated per-client
    account_id = models.CharField(max_length=20, unique=True) 
    name = models.CharField(max_length=255)      
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    
    history = HistoricalRecords()
    
    class Meta:
        # DELETED: unique_together = ('client', 'account_id')
        ordering = ['account_id']

    def __str__(self):
        return f"{self.account_id} - {self.name}"

# ====================================================================
# --- 2. JOURNAL ENTRY (The Header with Explicit FKs) ---
# ====================================================================
class JournalEntry(models.Model):
    # DELETED: client = models.ForeignKey(...)
    date = models.DateField()
    description = models.CharField(max_length=1000)
    reference_number = models.CharField(max_length=100, blank=True, null=True, help_text="Store Invoice No or Voucher No for safe-keeping")
    
    purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    bank = models.ForeignKey('cash.Bank', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    cash = models.ForeignKey('cash.Cash', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    journal_voucher = models.ForeignKey('tools.JournalVoucher', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    old = models.ForeignKey('tools.Old', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    adjustment = models.ForeignKey('tools.Adjustment', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    asset = models.ForeignKey('assets.Asset', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')

    created_at = models.DateTimeField(auto_now_add=True)
    
    history = HistoricalRecords()

    class Meta:
        ordering = ['-date', '-created_at']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(purchase__isnull=False, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=True, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=False, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=True, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=False, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=True, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=False, old__isnull=True, adjustment__isnull=True, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=False, adjustment__isnull=True, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=False, asset__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=True, asset__isnull=False) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True, adjustment__isnull=True, asset__isnull=True) # Manual Entry
                ),
                name='exclusive_source_document_constraint'
            )
        ]

    def clean(self):
        sources = [self.purchase, self.bank, self.cash, self.journal_voucher, self.old, getattr(self, 'adjustment', None), getattr(self, 'asset', None)]
        populated_sources = sum(1 for source in sources if source is not None)
        
        if populated_sources > 1:
            raise ValidationError(
                "A Journal Entry cannot be linked to multiple source documents simultaneously. "
                "Please select only ONE of: Purchase, Bank, Cash, Journal Voucher, Historical (Old), Adjustment, or Asset."
            )
            
    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"JE-{self.id} | {self.date} | {self.description}"

    @property
    def source_type(self):
        if self.purchase_id: return "Purchase"
        if self.bank_id: return "Bank"
        if self.cash_id: return "Cash Book"
        if self.journal_voucher_id: return "Journal Voucher"
        if self.old_id: return "Historical"
        if getattr(self, 'adjustment_id', None): return "Adjustment"
        if getattr(self, 'asset_id', None): return "Asset"
        return "Manual Entry"

# ====================================================================
# --- 3. JOURNAL LINE (The Debits and Credits) ---
# ====================================================================
class JournalLine(models.Model):
    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.CASCADE)
    
    description = models.CharField(max_length=1000, blank=True, null=True)
    debit = models.FloatField(default=0.0)
    credit = models.FloatField(default=0.0)

    def clean(self):
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debits and Credits cannot be negative numbers.")
        if self.debit > 0 and self.credit > 0:
            raise ValidationError("A single line cannot have both a debit and a credit. Use separate lines.")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.account.account_id} | Dr: {self.debit} | Cr: {self.credit}"     

class AccountMappingRule(models.Model):
    """Stores the AI trigger keywords and reasoning guidelines for each account."""
    account = models.ForeignKey('Account', on_delete=models.CASCADE)
    
    trigger_keywords = models.CharField(max_length=500, help_text="e.g., 'Vital drinking water, Rice for worker'")
    ai_guideline = models.TextField(help_text="Reasoning for the AI.")
    
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()
    
    def __str__(self):
        return f"Rule for {self.account.account_id}"

class ClientPromptMemo(models.Model):
    CATEGORY_CHOICES = [
        ('GENERAL', 'General & Universal Rules'),
        ('BANK_EXTRACTION', 'Bank Statement Extraction'),
        ('RECONCILIATION', 'GL Mapping & Reconciliation'),
        ('PURCHASE', 'Purchase & Expense Rules'),
        ('VENDOR_CUSTOMER', 'Vendor & Customer Rules'),
    ]
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='GENERAL')
    memo_text = models.TextField()
    history = HistoricalRecords()

    def __str__(self):
        return f"Memo ({self.category})"

# account/models.py

class DashboardSnapshot(models.Model):
    """
    Stores pre-calculated financial KPIs to prevent heavy runtime aggregations.
    Updated via scheduled background jobs.
    """
    calculated_at = models.DateTimeField(auto_now_add=True)
    period_label = models.CharField(max_length=50, help_text="e.g., May 2026 or YTD")
    
    # Financial Ratios & Metrics
    total_cash_usd = models.FloatField(default=0.0)
    total_ar_usd = models.FloatField(default=0.0)
    total_ap_usd = models.FloatField(default=0.0)
    net_profit_usd = models.FloatField(default=0.0)
    exchange_rate = models.IntegerField(null=True, blank=True, help_text="KHR to USD exchange rate")
    
    # JSON field for flexible charting data (e.g., 6-month trailing revenue)
    chart_data_payload = models.JSONField(default=dict, blank=True, null=True)
    
    # AI Generated Content
    ai_executive_summary = models.TextField(blank=True, null=True, help_text="AI generated summary of the snapshot")

    class Meta:
        ordering = ['-calculated_at']

    def __str__(self):
        return f"Snapshot - {self.period_label} ({self.calculated_at.strftime('%Y-%m-%d %H:%M')})"

class AgentNotification(models.Model):
    """
    The central communication ledger for all autonomous AAA agents.
    """
    AGENT_CHOICES = [
        ('RECON', 'Reconciliation Agent ⚖️'),
        ('TAX', 'Tax & Compliance Agent 🏛️'),
        ('ECON', 'Macro-Economic Agent 📈'),
        ('AUDIT', 'Internal Audit Agent 🔍'),
        ('SYSTEM', 'System Operations ⚙️'),
    ]

    SEVERITY_CHOICES = [
        ('INFO', 'Informational (Blue)'),
        ('SUCCESS', 'Success (Green)'),
        ('WARNING', 'Warning / Action Needed (Yellow)'),
        ('CRITICAL', 'Critical / Blocking (Red)'),
    ]

    agent_type = models.CharField(max_length=20, choices=AGENT_CHOICES)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='INFO')
    
    title = models.CharField(max_length=255, help_text="e.g., Missing VAT TIN Detected")
    message = models.TextField(help_text="The detailed explanation from the AI.")
    
    # Actionability: Where should the user click to fix this?
    action_url = models.CharField(max_length=500, blank=True, null=True, help_text="URL path to the relevant app view")
    action_label = models.CharField(max_length=50, blank=True, null=True, help_text="e.g., 'Review Invoices'")
    
    # State Management
    is_resolved = models.BooleanField(default=False, help_text="Has the human or AI resolved this issue?")
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_resolved', '-created_at']),
        ]

    def __str__(self):
        return f"[{self.get_severity_display()}] {self.get_agent_type_display()}: {self.title}"