from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    
    path('admin-tools/upload-rules/', views.upload_mapping_rules_view, name='upload_mapping_rules'),
    path('admin-tools/import-accounts/', views.import_accounts_view, name='import_accounts'),
    
    path('reports/trial-balance/', views.trial_balance_view, name='trial_balance'),
    path('reports/profit-and-loss/', views.profit_and_loss_view, name='profit_and_loss'),
    path('reports/balance-sheet/', views.balance_sheet_view, name='balance_sheet'),
    path('reports/general-ledger/', views.general_ledger_view, name='general_ledger_list'),
    path('reports/general-ledger/<str:account_id>/', views.account_ledger_detail_view, name='account_ledger_detail'),

    # Export URLs
    path('reports/trial-balance/export/', views.export_trial_balance, name='export_trial_balance'),
    path('reports/profit-and-loss/export/', views.export_profit_and_loss, name='export_profit_and_loss'),
    path('reports/balance-sheet/export/', views.export_balance_sheet, name='export_balance_sheet'),
    path('reports/general-ledger/export/', views.export_general_ledger_summary, name='export_general_ledger_summary'),
    path('reports/general-ledger/<str:account_id>/export/', views.export_account_ledger_detail, name='export_account_ledger_detail'),
]
