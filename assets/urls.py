from django.urls import path
from . import views

app_name = 'assets'

urlpatterns = [

    path('assets/', views.asset_dashboard, name='asset_dashboard'),
    path('assets/register/', views.register_asset, name='register_asset'),
    path('assets/depreciate/', views.run_monthly_depreciation, name='run_depreciation'),
    path('assets/<int:asset_id>/dispose/', views.dispose_asset, name='dispose_asset'),

]
