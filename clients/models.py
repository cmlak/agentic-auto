# clients/models.py
from django.db import models
from django_tenants.models import TenantMixin, DomainMixin

class Client(TenantMixin):
    name = models.CharField(max_length=100)
    created_on = models.DateField(auto_now_add=True)
    
    # default true, schema will be automatically created and synced when it is saved
    auto_create_schema = True 

class Domain(DomainMixin):
    pass

# ExchangeRate model
class ExchangeRate(models.Model):
    date = models.DateField(null=True, blank=True, unique=True)
    rate = models.PositiveIntegerField(default=0, null=True, blank=True)

    def __str__(self):
        return str(self.date) if self.date else "N/A"
