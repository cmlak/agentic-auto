from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from clients.models import Client
from django.http import HttpResponse, HttpResponseForbidden
from tools.tasks import backup_all_tenant_schemas  # Import your celery task
from clients.tasks import scrape_exchange_rate_nbc
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

    try:
        # Force the database routing connection wrapper back to the global infrastructure
        connection.set_schema_to_public()
    except Exception:
        pass

    # Hand off to Upstash instantly
    backup_all_tenant_schemas.delay() 
    
    return HttpResponse("Backup task successfully handed off to Celery worker!", status=200)


@csrf_exempt
def trigger_nbc_scraper_view(request):
    # We reuse the BACKUP_TRIGGER_TOKEN for convenience, or you can set a dedicated CRON_TRIGGER_TOKEN
    EXPECTED_TOKEN = os.environ.get('BACKUP_TRIGGER_TOKEN', 'my-super-secret-backup-token-123!')
    provided_token = request.GET.get('token')
    
    if provided_token != EXPECTED_TOKEN:
        return HttpResponseForbidden("Access Denied: Invalid Security Token")

    try:
        connection.set_schema_to_public()
    except Exception:
        pass

    scrape_exchange_rate_nbc.delay() 
    
    return HttpResponse("NBC Scraper task successfully handed off to Celery worker!", status=200)