from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

app_name = 'register'
urlpatterns = [

    path('index/', views.IndexView, name='main'),
    path('logout/', views.logout_request, name='logout'),
    path('login/', views.login_request, name='login'),
    path('registration/', views.registration_request, name='registration'),
    path('registration/update/', views.registration_update, name='registration_update'),

    ## profile
    path('profile/list/', views.ProfileListView.as_view(), name='profile_list'),
    path('profile/<int:pk>/', views.ProfileDetailView.as_view(), name='profile_detail'),
    
] 