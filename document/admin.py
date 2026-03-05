from django.contrib import admin
from .models import Document

# This line tells Django to show the Document model in the admin panel
admin.site.register(Document)