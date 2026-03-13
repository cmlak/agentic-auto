
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect


urlpatterns = [
    path('admin/', admin.site.urls),
    path('register/', include('register.urls')),
    path('tools/', include('tools.urls')),
    path('cash/', include('cash.urls')),
    path('account/', include('account.urls')),
    path('', lambda request: redirect('admin/', permanent=False)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)