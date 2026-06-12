from django.urls import path
from .views import test_db_connection, upload_financial_report_view, review_draft_rules_view

app_name = 'document'

urlpatterns = [
    path('test-db/', test_db_connection),
    path('upload-financial-report/', upload_financial_report_view, name='upload_financial_report'),
    path('review-rules/', review_draft_rules_view, name='review_draft_rules'),
]