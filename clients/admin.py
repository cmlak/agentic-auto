from django.contrib import admin
from clients.models import Client, Domain

# This line tells Django to show the Document model in the admin panel
admin.site.register(Client)
admin.site.register(Domain)
