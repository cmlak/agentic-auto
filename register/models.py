from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from tools.models import Client

DEPARTMENT_CHOICES = [
    ('accounting', 'Accounting Advisory'),
    ('audit', 'Audit Advisory'),
    ('tax', 'Tax Advisory'),
]

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    clients = models.ManyToManyField(Client, blank=True, related_name='authorized_profiles')
    department = models.CharField(max_length=100, choices=DEPARTMENT_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def get_absolute_url(self):
        return reverse('register:profile_detail', kwargs={'pk': self.pk})

    def __str__(self):
        return self.user.last_name + ' ' + self.user.first_name + ' - ' + self.department
