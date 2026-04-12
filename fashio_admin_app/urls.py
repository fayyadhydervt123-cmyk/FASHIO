from django.urls import path
from . import views

urlpatterns = [
    path('',views.admin_login, name='admin_login'),
    path('dashboard',views.admin_dashboard, name='admin_dashboard'),
    path('logout',views.admin_logout, name='admin_logout'),
    path('customers',views.admin_customerlist, name='admin_customerlist')
]
