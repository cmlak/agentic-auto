from django.urls import path
from .views import test_db_connection

urlpatterns = [
    path('test-db/', test_db_connection),
]