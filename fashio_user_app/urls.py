from django.urls import path
from . import views

urlpatterns = [
    path('',views.user_landing_dashboard, name='user_landing_dashboard'),
    path('dashboard',views.user_loggedin_dashboard, name='user_loggedin_dashboard'),
    path('login',views.user_login, name='user_login'),
    path('logout',views.user_logout,name='user_logout'),
    path('forgot-password',views.user_forgot_password, name='user_forgot_password'),
    path('verify-otp',views.user_verify_otp, name='user_verify_otp'),
    path('signup',views.user_signup, name='user_signup'),
    path('profile',views.user_profile, name='user_profile'),
    path('edit-profile',views.user_edit_profile,name='user_edit_profile'),
    path('add-address',views.user_add_address,name='user_add_address'),
    path('delete-address/<int:id>/',views.user_delete_address,name="user_delete_address")
]

