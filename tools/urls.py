from django.urls import path
from . import views

app_name = 'tools'

urlpatterns = [
    path('upload-tax-vendor/', views.process_vendor_tax_upload, name='upload_tax_vendor'),
    path('upload-success/', views.upload_success, name='upload_success'), 
    path('download-vendor/', views.download_vendor_csv, name='download_vendor_csv'),
]