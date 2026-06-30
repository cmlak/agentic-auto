from django.urls import path
from . import views
from . import views_capitalization_agent

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

    path('capitalization/upload/', views.capitalization_upload_view, name='capitalization_upload'),
    path('capitalization/review/', views.capitalization_review_view, name='capitalization_review'),
    path('capitalization/', views.capitalization_list_view, name='capitalization_list'),
    path('capitalization/<int:pk>/edit/', views.capitalization_edit_view, name='capitalization_edit'),
    path('capitalization/<int:pk>/delete/', views.capitalization_delete_view, name='capitalization_delete'),

    # New Agentic Capitalization Pipeline
    path('capitalization-agent/upload/', views_capitalization_agent.capitalization_agent_upload_view, name='capitalization_agent_upload'),
    path('capitalization-agent/review/', views_capitalization_agent.capitalization_agent_review_view, name='capitalization_agent_review'),
    path('capitalization-agent/', views_capitalization_agent.capitalization_agent_list_view, name='capitalization_agent_list'),
]
