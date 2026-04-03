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
    
    path('purchases/', views.PurchaseListView, name='purchase_list'),
    path('purchases/<int:pk>/', views.PurchaseDetailView.as_view(), name='purchase_detail'),
    path('purchases/<int:pk>/update/', views.PurchaseUpdateView.as_view(), name='purchase_update'),
    path('purchases/<int:pk>/delete/', views.PurchaseDeleteView.as_view(), name='purchase_delete'),
    path('purchases/export/', views.export_purchase_csv, name='purchase_export'),
    
    path('gl-migration/', views.gl_migration_upload_view, name='gl_migration_upload'),
    path('gl-migration/review/', views.gl_review_view, name='gl_review'),
    path('gl-migration/download/', views.gl_download_view, name='gl_download'),
    
    path('management/ai-costs/', views.ai_cost_dashboard, name='ai_cost_dashboard'),
    
    # Old Model CRUD URLs
    path('old-records/', views.OldListView, name='old_list'),
    path('old-records/new/', views.manual_old_entry_view, name='manual_old_entry'),
    path('old-records/<int:pk>/', views.OldDetailView.as_view(), name='old_detail'),
    path('old-records/<int:pk>/update/', views.OldUpdateView.as_view(), name='old_update'),
    path('old-records/<int:pk>/delete/', views.OldDeleteView.as_view(), name='old_delete'),

    # Balancika export
    path('export/balancika/', views.export_balancika_view, name='balancika_export'),

    # Engagement Letter
    path('proposals/upload/', views.upload_proposals_view, name='upload_proposals'),

    # Journal Voucher
    path('upload/tos/', views.process_tos_view, name='tos_upload'),
]