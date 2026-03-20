from django.urls import path
from . import views

app_name = 'account'

urlpatterns = [
    
    path('admin-tools/upload-rules/', views.upload_mapping_rules_view, name='upload_mapping_rules'),
    
    path('reports/trial-balance/', views.trial_balance_view, name='trial_balance'),
    path('reports/profit-and-loss/', views.profit_and_loss_view, name='profit_and_loss'),
    path('reports/balance-sheet/', views.balance_sheet_view, name='balance_sheet'),
    path('reports/general-ledger/', views.general_ledger_view, name='general_ledger_list'),
    path('reports/general-ledger/<str:account_id>/', views.account_ledger_detail_view, name='account_ledger_detail'),
]
