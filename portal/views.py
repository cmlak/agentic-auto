from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from clients.models import Client
from django.http import HttpResponse, HttpResponseForbidden
from tools.tasks import backup_all_tenant_schemas  # Import your celery task
import os


@login_required(login_url='/admin/login/') # Redirects to admin login if not authenticated
def client_dashboard(request):
    # Fetch all active clients, excluding the infrastructure 'public' schema
    clients = Client.objects.exclude(schema_name='public').order_by('name')
    
    return render(request, 'portal/dashboard.html', {'clients': clients})

def trigger_nightly_backup(request):
    """
    A public endpoint that allows Google Cloud Scheduler to kick off
    the background Celery backup task.
    """
    # 1. Simple Token Security: Matches the token we will give to Cloud Scheduler
    EXPECTED_TOKEN = os.environ.get('BACKUP_TRIGGER_TOKEN', 'my-super-secret-backup-token-123!')
    provided_token = request.GET.get('token')
    
    if provided_token != EXPECTED_TOKEN:
        return HttpResponseForbidden("Access Denied: Invalid Security Token")

    # 2. Drop the task into Upstash Redis via .delay()
    # This handoff takes less than a millisecond, so Cloud Run won't timeout.
    backup_all_tenant_schemas.delay() 
    
    return HttpResponse("Backup task successfully handed off to Celery worker!", status=200)