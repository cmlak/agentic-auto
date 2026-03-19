from django.contrib import admin
from .models import User, Profile

# This line tells Django to show the Document model in the admin panel
admin.site.register(Profile)
