from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from clients.models import Client
from django.http import HttpResponse, HttpResponseForbidden
from tools.tasks import backup_all_tenant_schemas  # Import your celery task
import os
from django.views.decorators.csrf import csrf_exempt
from django.db import connection


@login_required(login_url='/admin/login/') # Redirects to admin login if not authenticated
def client_dashboard(request):
    # Fetch all active clients, excluding the infrastructure 'public' schema
    clients = Client.objects.exclude(schema_name='public').order_by('name')
    
    return render(request, 'portal/dashboard.html', {'clients': clients})

@csrf_exempt
def trigger_nightly_backup(request):
    EXPECTED_TOKEN = os.environ.get('BACKUP_TRIGGER_TOKEN', 'my-super-secret-backup-token-123!')
    provided_token = request.GET.get('token')
    
    if provided_token != EXPECTED_TOKEN:
        return HttpResponseForbidden("Access Denied: Invalid Security Token")

    # FIX: Force Django to break out of the multi-tenant middleware loop
    # and execute the Celery task delegation directly from the 'public' base schema context
    connection.set_schema_to_public() 

    # Dispatch to Upstash Redis natively
    backup_all_tenant_schemas.delay() 
    
    return HttpResponse("Backup task successfully handed off to Celery worker!", status=200)