from django.db import models
from django.core.exceptions import ValidationError
from django.db.models import Q
from tools.models import Client

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

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='accounts')
    account_id = models.CharField(max_length=20) 
    name = models.CharField(max_length=255)      
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES)
    
    class Meta:
        unique_together = ('client', 'account_id')
        ordering = ['account_id']

    def __str__(self):
        return f"{self.account_id} - {self.name}"

# ====================================================================
# --- 2. JOURNAL ENTRY (The Header with Explicit FKs) ---
# ====================================================================
class JournalEntry(models.Model):
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    date = models.DateField()
    description = models.CharField(max_length=1000)
    reference_number = models.CharField(max_length=100, blank=True, null=True, help_text="Store Invoice No or Voucher No for safe-keeping")
    
    # --- THE SPARSE MATRIX (Explicit Foreign Keys) ---
    # We use SET_NULL so if a source document is deleted, the GL entry remains intact 
    # (though in strict accounting, you might use PROTECT to forbid deletion of posted documents).
    purchase = models.ForeignKey('tools.Purchase', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    bank = models.ForeignKey('cash.Bank', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    cash = models.ForeignKey('cash.Cash', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    journal_voucher = models.ForeignKey('tools.JournalVoucher', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    old = models.ForeignKey('tools.Old', on_delete=models.CASCADE, null=True, blank=True, related_name='journal_entries')
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']
        # DATABASE-LEVEL PROTECTION:
        # Enforces that only ONE source document can be attached, or ALL must be null (for manual entries).
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(purchase__isnull=False, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=False, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=False, journal_voucher__isnull=True, old__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=False, old__isnull=True) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=False) |
                    models.Q(purchase__isnull=True, bank__isnull=True, cash__isnull=True, journal_voucher__isnull=True, old__isnull=True) # Manual Entry
                ),
                name='exclusive_source_document_constraint'
            )
        ]

    def clean(self):
        """
        APPLICATION-LEVEL PROTECTION:
        Validates the model before saving via forms or standard ORM `save()` calls.
        """
        sources = [self.purchase, self.bank, self.cash, self.journal_voucher, self.old]
        # Count how many sources are actually populated
        populated_sources = sum(1 for source in sources if source is not None)
        
        if populated_sources > 1:
            raise ValidationError(
                "A Journal Entry cannot be linked to multiple source documents simultaneously. "
                "Please select only ONE of: Purchase, Bank, Cash, Journal Voucher, or Historical (Old)."
            )
            
    def save(self, *args, **kwargs):
        # Force the clean() method to run every time save() is called programmatically
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"JE-{self.id} | {self.date} | {self.description}"

    @property
    def source_type(self):
        """Helper property to easily identify what kind of entry this is in templates."""
        if self.purchase_id: return "Purchase"
        if self.bank_id: return "Bank"
        if self.cash_id: return "Cash Book"
        if self.journal_voucher_id: return "Journal Voucher"
        if self.old_id: return "Historical"
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
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='mapping_rules')
    account = models.ForeignKey('Account', on_delete=models.CASCADE)
    
    trigger_keywords = models.CharField(max_length=500, help_text="e.g., 'Vital drinking water, Rice for worker'")
    ai_guideline = models.TextField(help_text="Reasoning for the AI.")
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ('client', 'account')

    def __str__(self):
        return f"Rule for {self.account.account_id} ({self.client.name})"

class ClientPromptMemo(models.Model):
    CATEGORY_CHOICES = [
        ('GENERAL', 'General & Universal Rules'),
        ('BANK_EXTRACTION', 'Bank Statement Extraction'),
        ('RECONCILIATION', 'GL Mapping & Reconciliation'),
        ('PURCHASE', 'Purchase & Expense Rules'),
        ('VENDOR_CUSTOMER', 'Vendor & Customer Rules'),
    ]
    client = models.ForeignKey(Client, on_delete=models.CASCADE)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='GENERAL')
    memo_text = models.TextField()

    def __str__(self):
        return f"Memo for {self.client.name} ({self.category})"
