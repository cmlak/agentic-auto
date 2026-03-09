from django.contrib import admin
from .models import Vendor

# This line tells Django to show the Document model in the admin panel
admin.site.register(Vendor)