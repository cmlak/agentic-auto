from django.contrib import admin
from .models import Account, AccountMappingRule, ClientPromptMemo

# This line tells Django to show the Document model in the admin panel
admin.site.register(Account)
admin.site.register(AccountMappingRule)
admin.site.register(ClientPromptMemo)
