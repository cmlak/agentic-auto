from django.contrib import admin
from .models import Vendor, Client

# This line tells Django to show the Document model in the admin panel
admin.site.register(Vendor)
admin.site.register(Client)