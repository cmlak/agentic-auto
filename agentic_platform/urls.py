
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from register.views import IndexView
from portal.views import client_dashboard, trigger_nightly_backup, trigger_nbc_scraper_view


urlpatterns = [
    path('admin/', admin.site.urls),
    path('register/', include('register.urls')),
    path('tools/', include('tools.urls')),
    path('cash/', include('cash.urls')),
    path('account/', include('account.urls')),
    path('sale/', include('sale.urls')),
    path('assets/', include('assets.urls')),
    path('document/', include('document.urls')),
    # path('', IndexView, name='main'),
    # The root domain (localhost:8000 / your base URL) goes to the lobby
    path('', client_dashboard, name='portal_dashboard'),
    # Map the URL that Cloud Scheduler is currently looking for
    # path('api/trigger-backup/', trigger_nightly_backup, name='trigger_backup'),
    path('api/trigger-backup/', trigger_nightly_backup, name='trigger_nightly_backup'),
    path('api/v1/cron/scrape-nbc/', trigger_nbc_scraper_view, name='cron_scrape_nbc'),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)