from django.urls import path
from . import views

app_name = 'tools'

urlpatterns = [
    # path('upload-tax-vendor/', views.process_vendor_tax_upload, name='upload_tax_vendor'),
    # path('upload-success/', views.upload_success, name='upload_success'), 
    # path('download-vendor/', views.download_vendor_csv, name='download_vendor_csv'),

    path('process-invoices/', views.invoice_ai_upload_view, name='invoice_upload'),
    path('review-invoices/', views.review_invoices, name='review_invoices'),
    path('invoice-success/', views.invoice_download_view, name='invoice_download'),
    path('download-invoice-report/', views.download_invoice_report, name='download_invoice_report'),
    path('invoice/manual-entry/', views.manual_invoice_entry_view, name='manual_invoice_entry'),
    
    path('export/purchases/<int:client_id>/', views.export_purchase_invoices, name='export_purchases'),
    path('export/purchases/success/', views.purchase_export_success_view, name='purchase_export_success'),
    path('export/purchases/download/', views.download_exported_purchases, name='download_exported_purchases'),
    
    path('management/ai-costs/', views.ai_cost_dashboard, name='ai_cost_dashboard'),
]