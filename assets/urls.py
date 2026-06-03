from django.urls import path
from . import views

app_name = 'assets'

urlpatterns = [

    path('assets/', views.asset_dashboard, name='asset_dashboard'),
    path('assets/register/', views.register_asset, name='register_asset'),
    path('assets/depreciate/', views.run_monthly_depreciation, name='run_depreciation'),
    path('assets/<int:asset_id>/dispose/', views.dispose_asset, name='dispose_asset'),
    
    path('assets/list/', views.AssetListView.as_view(), name='asset_list'),
    # path('assets/create/', views.AssetCreateView.as_view(), name='asset_create'),
    path('assets/<int:pk>/update/', views.AssetUpdateView.as_view(), name='asset_update'),
    path('assets/<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
    path('assets/<int:pk>/schedule/', views.asset_depreciation_schedule, name='asset_depreciation_schedule'),
    path('assets/<int:pk>/schedule/export/', views.export_asset_depreciation_schedule, name='export_asset_depreciation_schedule'),
    path('assets/export/', views.export_assets, name='export_assets'),

    path('depreciation-entries/', views.DepreciationEntryListView.as_view(), name='depreciation_entry_list'),
    path('depreciation-entries/create/', views.DepreciationEntryCreateView.as_view(), name='depreciation_entry_create'),
    path('depreciation-entries/<int:pk>/update/', views.DepreciationEntryUpdateView.as_view(), name='depreciation_entry_update'),
    path('depreciation-entries/<int:pk>/delete/', views.DepreciationEntryDeleteView.as_view(), name='depreciation_entry_delete'),
    path('depreciation-entries/export/', views.export_depreciation_entries, name='export_depreciation_entries'),

    path('asset-disposals/', views.AssetDisposalListView.as_view(), name='asset_disposal_list'),
    path('asset-disposals/<int:pk>/update/', views.AssetDisposalUpdateView.as_view(), name='asset_disposal_update'),
    path('asset-disposals/<int:pk>/delete/', views.AssetDisposalDeleteView.as_view(), name='asset_disposal_delete'),
    path('asset-disposals/export/', views.export_asset_disposals, name='export_asset_disposals'),
]
