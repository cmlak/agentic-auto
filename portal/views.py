from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from clients.models import Client

@login_required(login_url='/admin/login/') # Redirects to admin login if not authenticated
def client_dashboard(request):
    # Fetch all active clients, excluding the infrastructure 'public' schema
    clients = Client.objects.exclude(schema_name='public').order_by('name')
    
    return render(request, 'portal/dashboard.html', {'clients': clients})