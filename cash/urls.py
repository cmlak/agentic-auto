from django.urls import path
from . import views

app_name = 'cash'

urlpatterns = [
    path('bank-upload/', views.bank_ai_upload_view, name='bank_upload'),
    path('bank-review/', views.bank_review_view, name='bank_review'),
    path('bank-success/', views.bank_download_view, name='bank_download'),
    path('download-bank-report/', views.download_bank_report, name='download_bank_report'),
    
    path('export/bank/<int:client_id>/', views.export_bank_transactions, name='export_banks'),
    path('export/bank/success/', views.bank_export_success_view, name='bank_export_success'),
    path('export/bank/download/', views.download_exported_banks, name='download_exported_banks'),

    path('cash-upload/', views.cash_upload_view, name='cash_upload'),
    path('cash-review/', views.cash_review_view, name='cash_review'),
    path('cash-success/', views.cash_download_view, name='cash_download'),
    path('download-preliminary-cash/', views.download_preliminary_cash_report, name='download_preliminary_cash'),
    
    path('export/cash/<int:client_id>/', views.export_cash_transactions, name='export_cash'),
    path('export/cash/success/', views.cash_export_success_view, name='cash_export_success'),
    path('export/cash/download/', views.download_exported_cash, name='download_exported_cash'),
    
    # BANK CRUD
    path('bank/', views.BankListView, name='bank_list'),
    path('bank/manual-entry/', views.manual_bank_entry_view, name='manual_bank_entry'),
    path('bank/<int:pk>/', views.BankDetailView.as_view(), name='bank_detail'),
    path('bank/<int:pk>/update/', views.BankUpdateView.as_view(), name='bank_update'),
    path('bank/<int:pk>/delete/', views.BankDeleteView.as_view(), name='bank_delete'),
    path('bank/export-csv/', views.export_bank_csv, name='bank_export_csv'),

    # CASH CRUD
    path('cash/', views.CashListView, name='cash_list'),
    path('cash/manual-entry/', views.manual_cash_entry_view, name='manual_cash_entry'),
    path('cash/<int:pk>/', views.CashDetailView.as_view(), name='cash_detail'),
    path('cash/<int:pk>/update/', views.CashUpdateView.as_view(), name='cash_update'),
    path('cash/<int:pk>/delete/', views.CashDeleteView.as_view(), name='cash_delete'),
    path('cash/export-csv/', views.export_cash_csv, name='cash_export_csv'),
]