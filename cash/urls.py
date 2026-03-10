from django.urls import path
from . import views

app_name = 'cash'

urlpatterns = [
    path('bank-upload/', views.bank_ai_upload_view, name='bank_upload'),
    path('bank-review/', views.bank_review_view, name='bank_review'),
    path('bank-success/', views.bank_download_view, name='bank_download'),
    path('download-bank-report/', views.download_bank_report, name='download_bank_report'),

    path('cash-upload/', views.cash_upload_view, name='cash_upload'),
    path('cash-review/', views.cash_review_view, name='cash_review'),
    path('cash-success/', views.cash_download_view, name='cash_download'),
    path('download-cash-report/', views.download_cash_report, name='download_cash_report'),
]